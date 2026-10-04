# The control port — command recordings and the assistant forward

Two optional features hand a sentence to another program instead of typing it:

- **Command recordings** (`[os_control]`) — press `Y` as the last key of the voice
  hotkey chord (`Ctrl+Shift+Alt+Y` by default) and that recording is sent as a command,
  for example to a window switcher that understands "focus 3" or "bring up the terminal".
- **Assistant forward** (`[assistant]`) — "nexus assistant &lt;anything&gt;" is sent to a
  voice assistant, for example "nexus assistant remember to buy milk".

NexusVox only routes the text. The program on the other end is **not part of NexusVox**:
you run it yourself, and any program that speaks the contract below works. Both features
are off by default; with them off, `Ctrl+Shift+Alt+Y` types a plain `y` and "nexus
assistant ..." is typed like any other sentence.

## Configuration

`config.toml`:

```toml
[os_control]
enabled = false
command_key = "Y"            # or "OEM_102", the "<" key on a German keyboard
host = "127.0.0.1"
port = 49730
service = "os-control"
timeout_s = 5.0

[assistant]
enabled = false
host = "127.0.0.1"
port = 49730
service = "voice-assistant"
timeout_s = 5.0
```

The command key only counts when it is pressed during a recording with every hotkey
modifier held, so it completes the chord. Anything else — `Shift+Y`, `AltGr+Y`, or `Y`
pressed before the last modifier — types normally. If you change the voice hotkey in
Settings → Voice Input, the command chord changes with it (`Ctrl+Alt` makes it
`Ctrl+Alt+Y`).

Both features may share one port: the `service` name tells your program which one is
calling.

## The contract

Plain TCP, one request per connection.

1. NexusVox connects to `host:port`.
2. It sends one UTF-8 line without a trailing newline: `send <service> <text>`.
3. It shuts down its write side, so reading until end of stream gives the whole request.
4. Your program answers with one line of UTF-8 text and closes the connection. NexusVox
   reads at most 4 KB and strips surrounding whitespace.

A reply that starts with `error:` means your program refused the command; NexusVox plays
the error beep and logs the reply. Any other reply, including an empty one, counts as
success. No connection, a refused connection or no answer within `timeout_s` counts as
"not reachable".

### Command recordings (`service = "os-control"`)

| Request                    | When                                                                                   |
|----------------------------|----------------------------------------------------------------------------------------|
| `send os-control ::show`   | The moment the command key is pressed, so your program can show a hint while the user speaks. The reply is ignored. |
| `send os-control <text>`   | After the recording, with the transcript.                                              |
| `send os-control ::hide`   | Instead of the text when the transcript is empty, and when the recording was skipped or failed. |

Every `::show` is followed by either a `<text>` or a `::hide` request. A command
recording is **never typed**: if your program is not reachable or replies `error:`, the
error beep plays and the text stays only in the NexusVox history, saved as
`[os-control] <text>`.

### Assistant forward (`service = "voice-assistant"`)

| Request                         | When                                                         |
|---------------------------------|--------------------------------------------------------------|
| `send voice-assistant <text>`   | The user said "nexus assistant &lt;text&gt;"; `<text>` is the rest of the sentence. |

The text is saved in the history as `[assistant] <text>` and not typed. If your program
is not reachable, the error beep plays and the full sentence is typed at the cursor as
usual, so nothing is lost.

## A minimal server

```python
import socketserver


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        request = self.rfile.read().decode("utf-8")  # NexusVox shuts its write side
        _verb, service, text = request.split(" ", 2)
        print(f"{service}: {text}")
        self.wfile.write(b"ok")


with socketserver.TCPServer(("127.0.0.1", 49730), Handler) as server:
    server.serve_forever()
```

## Reference implementation

The maintainer runs the Controller from a private personal-tooling repo: one process on
port 49730 that routes `os-control` to a window switcher (numbered overlay on
`::show`, "focus 3" or "bring up the terminal" brings that window to the front) and
`voice-assistant` to a note-taking voice assistant. It is not published. Any program
that honours the contract above works the same way.
