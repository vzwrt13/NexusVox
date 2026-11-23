"""Who signed a file, and is the signature intact? Windows' own Authenticode check, read through ctypes.

`check_signature` asks `WinVerifyTrust` whether the embedded signature is valid and chains to a trusted root (the
revocation of every certificate in the chain but the root included; when that cannot be looked up, because a network
blocks the CRL and OCSP servers, the check is repeated without it and the answer says so), then reads the signer's
name and whether the signature carries a timestamp from the PKCS #7 block itself. Nothing here runs the
file. Windows only: elsewhere every file reads as not signed.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Signature:
    """What `check_signature` found. `valid` alone says nothing about who signed: compare `signer` as well."""

    valid: bool
    signer: str | None = None  # the subject's common name (organisation as a fallback) of the signing certificate
    organisation: str | None = None
    timestamped: bool = False
    reason: str = ""  # why it is not valid, for the log; empty when valid
    revocation_checked: bool = True  # False when the revocation servers could not be reached (never when revoked)


# WinVerifyTrust: the generic Authenticode policy, no UI, the whole chain checked for revocation except the root.
_WTD_UI_NONE = 2
_WTD_REVOKE_NONE = 0
_WTD_REVOKE_WHOLECHAIN = 1
_WTD_CHOICE_FILE = 1
_WTD_STATEACTION_IGNORE = 0
_WTD_REVOCATION_CHECK_NONE = 0x10
_WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT = 0x80
# "Could not find out whether it is revoked", as opposed to CERT_E_REVOKED (0x800B010C), which always refuses:
# CERT_E_REVOCATION_FAILURE, CRYPT_E_REVOCATION_OFFLINE, CRYPT_E_NO_REVOCATION_CHECK.
_REVOCATION_UNKNOWN = {0x800B010E, 0x80092013, 0x80092012}

# Reading the PKCS #7 block.
_CERT_QUERY_OBJECT_FILE = 1
_CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED = 1 << 10
_CERT_QUERY_FORMAT_FLAG_BINARY = 1 << 1
_CMSG_SIGNER_INFO_PARAM = 6
_CMSG_SIGNER_CERT_INFO_PARAM = 7
_ENCODING = 0x00000001 | 0x00010000  # X509_ASN_ENCODING | PKCS_7_ASN_ENCODING
_CERT_FIND_SUBJECT_CERT = 0x000B0000
_CERT_NAME_ATTR_TYPE = 3
_CERT_NAME_SIMPLE_DISPLAY_TYPE = 4
_OID_ORGANIZATION = b"2.5.4.10"
# An unauthenticated attribute with either OID is the timestamp: RFC 3161, or the older Authenticode countersignature.
_TIMESTAMP_OIDS = {b"1.3.6.1.4.1.311.3.3.1", b"1.2.840.113549.1.9.6"}

# The answers of WinVerifyTrust that a user can act on; any other is logged as its number.
_TRUST_ERRORS = {
    0x800B0100: "the file is not signed",  # TRUST_E_NOSIGNATURE
    0x800B0003: "the file is not a signed program",  # TRUST_E_SUBJECT_FORM_UNKNOWN
    0x80096010: "the file was changed after it was signed",  # TRUST_E_BAD_DIGEST
    0x80096019: "the signature is broken",  # TRUST_E_CERT_SIGNATURE
    0x800B0109: "the certificate does not chain to a trusted root",  # CERT_E_UNTRUSTEDROOT
    0x800B010C: "the certificate was revoked",  # CERT_E_REVOKED
    0x800B010E: "the revocation of the certificate could not be checked",  # CERT_E_REVOCATION_FAILURE
    0x800B0101: "the certificate has expired and the signature has no timestamp",  # CERT_E_EXPIRED
    0x800B0111: "the publisher is blocked on this PC",  # TRUST_E_EXPLICIT_DISTRUST
    0x800B0004: "the subject is not trusted",  # TRUST_E_SUBJECT_NOT_TRUSTED
    0x80092003: "the file could not be read",  # CRYPT_E_FILE_ERROR
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# {00AAC56B-CD44-11d0-8CC2-00C04FC295EE}
_WINTRUST_ACTION_GENERIC_VERIFY_V2 = _GUID(
    0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE)
)


class _WintrustFileInfo(ctypes.Structure):
    _fields_ = [
        ("cbStruct", ctypes.c_uint32),
        ("pcwszFilePath", ctypes.c_wchar_p),
        ("hFile", ctypes.c_void_p),
        ("pgKnownSubject", ctypes.c_void_p),
    ]


class _WintrustData(ctypes.Structure):
    _fields_ = [
        ("cbStruct", ctypes.c_uint32),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", ctypes.c_uint32),
        ("fdwRevocationChecks", ctypes.c_uint32),
        ("dwUnionChoice", ctypes.c_uint32),
        ("pFile", ctypes.POINTER(_WintrustFileInfo)),
        ("dwStateAction", ctypes.c_uint32),
        ("hWVTStateData", ctypes.c_void_p),
        ("pwszURLReference", ctypes.c_wchar_p),
        ("dwProvFlags", ctypes.c_uint32),
        ("dwUIContext", ctypes.c_uint32),
        ("pSignatureSettings", ctypes.c_void_p),
    ]


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


class _AlgorithmIdentifier(ctypes.Structure):
    _fields_ = [("pszObjId", ctypes.c_char_p), ("Parameters", _Blob)]


class _Attribute(ctypes.Structure):
    _fields_ = [("pszObjId", ctypes.c_char_p), ("cValue", ctypes.c_uint32), ("rgValue", ctypes.POINTER(_Blob))]


class _Attributes(ctypes.Structure):
    _fields_ = [("cAttr", ctypes.c_uint32), ("rgAttr", ctypes.POINTER(_Attribute))]


class _SignerInfo(ctypes.Structure):
    """CMSG_SIGNER_INFO: only the unauthenticated attributes are read (that is where the timestamp sits)."""

    _fields_ = [
        ("dwVersion", ctypes.c_uint32),
        ("Issuer", _Blob),
        ("SerialNumber", _Blob),
        ("HashAlgorithm", _AlgorithmIdentifier),
        ("HashEncryptionAlgorithm", _AlgorithmIdentifier),
        ("EncryptedHash", _Blob),
        ("AuthAttrs", _Attributes),
        ("UnauthAttrs", _Attributes),
    ]


@functools.cache
def _apis():
    """wintrust and crypt32 with their prototypes declared once, so a 64-bit handle is never cut to an int."""
    u32, vp, wp = ctypes.c_uint32, ctypes.c_void_p, ctypes.c_wchar_p
    wintrust = ctypes.WinDLL("wintrust")
    wintrust.WinVerifyTrust.argtypes = [vp, ctypes.POINTER(_GUID), ctypes.POINTER(_WintrustData)]
    wintrust.WinVerifyTrust.restype = ctypes.c_long
    crypt32 = ctypes.WinDLL("crypt32")
    crypt32.CryptQueryObject.argtypes = [u32, vp, u32, u32, u32, vp, vp, vp, vp, vp, vp]
    crypt32.CryptQueryObject.restype = ctypes.c_int
    crypt32.CryptMsgGetParam.argtypes = [vp, u32, u32, vp, ctypes.POINTER(u32)]
    crypt32.CryptMsgGetParam.restype = ctypes.c_int
    crypt32.CertFindCertificateInStore.argtypes = [vp, u32, u32, u32, vp, vp]
    crypt32.CertFindCertificateInStore.restype = vp
    crypt32.CertGetNameStringW.argtypes = [vp, u32, u32, vp, wp, u32]
    crypt32.CertGetNameStringW.restype = u32
    crypt32.CertFreeCertificateContext.argtypes = [vp]
    crypt32.CertCloseStore.argtypes = [vp, u32]
    crypt32.CryptMsgClose.argtypes = [vp]
    return wintrust, crypt32


def _verify_trust(path: str, revocation: bool = True) -> int:
    """WinVerifyTrust's answer for the file: 0 when the signature is valid and trusted, else an HRESULT."""
    wintrust, _ = _apis()
    file_info = _WintrustFileInfo(ctypes.sizeof(_WintrustFileInfo), path, None, None)
    data = _WintrustData()
    data.cbStruct = ctypes.sizeof(_WintrustData)
    data.dwUIChoice = _WTD_UI_NONE
    data.fdwRevocationChecks = _WTD_REVOKE_WHOLECHAIN if revocation else _WTD_REVOKE_NONE
    data.dwUnionChoice = _WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = _WTD_STATEACTION_IGNORE
    data.dwProvFlags = _WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT if revocation else _WTD_REVOCATION_CHECK_NONE
    # INVALID_HANDLE_VALUE as hwnd: no UI may ever be shown.
    return wintrust.WinVerifyTrust(ctypes.c_void_p(-1), ctypes.byref(_WINTRUST_ACTION_GENERIC_VERIFY_V2), data) & (
        0xFFFFFFFF
    )


def _msg_param(crypt32, msg: int, param: int) -> ctypes.Array | None:
    size = ctypes.c_uint32(0)
    if not crypt32.CryptMsgGetParam(msg, param, 0, None, ctypes.byref(size)) or not size.value:
        return None
    buffer = (ctypes.c_byte * size.value)()
    if not crypt32.CryptMsgGetParam(msg, param, 0, buffer, ctypes.byref(size)):
        return None
    return buffer


def _cert_name(crypt32, cert: int, kind: int, type_para: object = None) -> str | None:
    para = ctypes.c_char_p(type_para) if type_para is not None else None
    length = crypt32.CertGetNameStringW(cert, kind, 0, para, None, 0)
    if length <= 1:
        return None
    buffer = ctypes.create_unicode_buffer(length)
    crypt32.CertGetNameStringW(cert, kind, 0, para, buffer, length)
    return buffer.value.strip() or None


def _read_signer(path: str) -> tuple[str | None, str | None, bool]:
    """(common name, organisation, timestamped) of the file's primary signer; (None, None, False) if unreadable."""
    _, crypt32 = _apis()
    store, msg = ctypes.c_void_p(), ctypes.c_void_p()
    found = crypt32.CryptQueryObject(
        _CERT_QUERY_OBJECT_FILE,
        ctypes.c_wchar_p(path),
        _CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
        _CERT_QUERY_FORMAT_FLAG_BINARY,
        0,
        None,
        None,
        None,
        ctypes.byref(store),
        ctypes.byref(msg),
        None,
    )
    if not found:
        return None, None, False
    cert = None
    try:
        timestamped = False
        signer_info = _msg_param(crypt32, msg.value, _CMSG_SIGNER_INFO_PARAM)
        if signer_info is not None:
            info = ctypes.cast(signer_info, ctypes.POINTER(_SignerInfo)).contents
            attrs = info.UnauthAttrs
            timestamped = any(attrs.rgAttr[i].pszObjId in _TIMESTAMP_OIDS for i in range(attrs.cAttr))
        cert_info = _msg_param(crypt32, msg.value, _CMSG_SIGNER_CERT_INFO_PARAM)
        if cert_info is None:
            return None, None, timestamped
        cert = crypt32.CertFindCertificateInStore(store.value, _ENCODING, 0, _CERT_FIND_SUBJECT_CERT, cert_info, None)
        if not cert:
            return None, None, timestamped
        name = _cert_name(crypt32, cert, _CERT_NAME_SIMPLE_DISPLAY_TYPE)
        organisation = _cert_name(crypt32, cert, _CERT_NAME_ATTR_TYPE, _OID_ORGANIZATION)
        return name, organisation, timestamped
    finally:
        if cert:
            crypt32.CertFreeCertificateContext(cert)
        if store.value:
            crypt32.CertCloseStore(store.value, 0)
        if msg.value:
            crypt32.CryptMsgClose(msg.value)


def check_signature(path: str | Path) -> Signature:
    """The Authenticode signature of `path`: valid and trusted, who signed it, and whether it has a timestamp."""
    if sys.platform != "win32":
        return Signature(False, reason="signatures can only be checked on Windows")
    path = str(Path(path).resolve())
    try:
        status, revocation_checked = _verify_trust(path), True
        if status in _REVOCATION_UNKNOWN:
            logger.warning("Revocation of the signer could not be checked (0x%08X); checking without it", status)
            status, revocation_checked = _verify_trust(path, revocation=False), False
        signer, organisation, timestamped = _read_signer(path) if status == 0 else (None, None, False)
    except OSError as exc:  # a DLL or entry point missing: treat it as unverifiable, never as valid
        logger.warning("The signature check could not run: %s", exc)
        return Signature(False, reason="the signature check could not run")
    if status != 0:
        return Signature(False, reason=_TRUST_ERRORS.get(status, f"Windows refused the signature (0x{status:08X})"))
    return Signature(
        True, signer=signer, organisation=organisation, timestamped=timestamped, revocation_checked=revocation_checked
    )
