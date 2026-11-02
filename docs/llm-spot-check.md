# Spot check: 20 items for a human read

## 1. Polish, `gemma4:e2b` on cpu (judge found a problem)
- Input: Anna kümmert sich um die Vorlage, Ben um den Upload-Fehler und ich spreche mit dem Kunden.
- Instruction: Mach daraus eine Aufgabenliste
- Output: Aufgabenliste:
* Anna: kümmern sich um die Vorlage
* Ben: kümmern sich um den Upload-Fehler
* Ich: sprechen mit dem Kunden
- Judge: meaning 1, language 1, invented-nothing 0, formatting 0 - The tool added a heading and failed to correct the grammar of the verbs.

## 2. Polish, `gemma4:e2b` on cpu (judge found a problem)
- Input: Hey, bist du heute Abend dabei? Wir wollen Pizza bestellen.
- Instruction: Mach es förmlicher
- Output: Sehr geehrte/r [Name einfügen, falls bekannt], nehmen Sie heute Abend teil? Wir beabsichtigen, Pizza zu bestellen.
- Judge: meaning 1, language 1, invented-nothing 0, formatting 0 - The output includes a placeholder and an unnecessary greeting not present in the original text.

## 3. Polish, `gemma4:e2b` on cpu (judge found a problem)
- Input: Der Server ist ausgefallen und wir wissen noch nicht warum Ben schaut es sich an
- Instruction: Fasse das kürzer
- Output: Der Server ist ausgefallen, der Grund ist unbekannt.
- Judge: meaning 0, language 1, invented-nothing 1, formatting 1 - The name Ben was omitted.

## 4. Polish, `gemma4:e2b` on cpu (judge found a problem)
- Input: Entschuldigung für die späte Antwort, ich war im Urlaub.
- Instruction: Mach es weniger entschuldigend
- Output: Entschuldigung für die späte Antwort, ich war im Urlaub.
- Judge: meaning 1, language 1, invented-nothing 1, formatting 0 - The tool failed to follow the instruction to make it less apologetic.

## 5. Polish, `gemma4:e4b` on cpu (judge found a problem)
- Input: Hey, are you in tonight? We're ordering pizza.
- Instruction: Make it more formal
- Output: Sind Sie heute Abend dabei? Wir bestellen Pizza.
- Judge: meaning 1, language 0, invented-nothing 1, formatting 1 - The output is in German while the input text was in English.

## 6. Cleanup light, `gemma4:e2b` on cpu (judge found a problem)
- Input: I I found the bug in the upload last night it only happens with files larger than twenty megabytes
- Output: I found the bug in the upload last night. It only happens with files larger than twenty megabytes.
- Judge: meaning 1, language 1, invented-nothing 1, formatting 0 - The output retains the accidental repetition of 'I' at the beginning.

## 7. Polish, `gemma4:e4b` on cpu (judge found a problem)
- Input: I wanted to ask if you maybe have time tomorrow we need to discuss the project because it is important
- Instruction: Make it more formal
- Output: Ich möchte anfragen, ob Sie morgen Zeit hätten, da wir das Projekt besprechen müssen, weil es von Bedeutung ist.
- Judge: meaning 1, language 0, invented-nothing 1, formatting 1 - The output is in German while the input text was in English.

## 8. Cleanup medium, `gemma4:e4b` on cpu (judge found a problem)
- Input: I'm on the train right now and I'll probably be like ten minutes late sorry about that
- Output: I'm on the train right now, and I'll probably be about ten minutes late. Sorry about that.
- Judge: meaning 0, language 1, invented-nothing 1, formatting 1 - The word 'about' was substituted for 'like'.

## 9. Cleanup medium, `gemma4:e4b` on cpu (judge found a problem)
- Input: Okay so the cost is about twenty four hundred dollars before tax and uh that already includes the discount
- Output: Okay, so the cost is about two thousand four hundred dollars before tax, and that already includes the discount.
- Judge: meaning 0, language 1, invented-nothing 1, formatting 1 - The number 'twenty four hundred' was changed to 'two thousand four hundred'.

## 10. Polish, `gemma4:e4b` on cpu (judge found a problem)
- Input: Please note that the office is closed on public holidays and requests are only handled on the next working day.
- Instruction: Summarise this in one sentence
- Output: Das Büro ist an Feiertagen geschlossen und Anfragen werden erst am nächsten Arbeitstag bearbeitet.
- Judge: meaning 1, language 0, invented-nothing 1, formatting 1 - The output is in German while the input text is in English.

## 11. Polish, `gemma4:e2b` on cpu (judge found a problem)
- Input: Sorry for the late reply, I was on vacation.
- Instruction: Make it less apologetic
- Output: I apologize for the late reply; I was on vacation.
- Judge: meaning 1, language 1, invented-nothing 1, formatting 0 - The output remains apologetic instead of making it less so.

## 12. Cleanup medium, `gemma4:e2b` on cpu (judge found a problem)
- Input: That was in twenty nineteen no wait twenty twenty when we switched to the new version
- Output: That was in twenty twenty when we switched to the new version.
- Judge: meaning 0, language 1, invented-nothing 1, formatting 1 - The fact 'twenty nineteen' was removed instead of being corrected or kept as a false start.

## 13. Polish, `gemma4:e4b` on cpu (judge found a problem)
- Input: The conference takes place from March 5 to 7 in Hamburg. Registration is open until the end of January.
- Instruction: Turn this into an invitation
- Output: Wir laden Sie herzlich zur Konferenz vom 5. bis 7. März in Hamburg ein. Die Anmeldung ist bis Ende Januar möglich.
- Judge: meaning 1, language 0, invented-nothing 1, formatting 1 - The output is in German while the input text was in English.

## 14. Polish, `gemma4:e4b` on cpu (judge found a problem)
- Input: We migrated the database. No data was lost. We will delete the old tables next week.
- Instruction: Write it for a customer without technical terms
- Output: Wir haben die Datenumstellung durchgeführt. Es ist kein Datenverlust aufgetreten. Die alten Tabellen werden nächste Woche gelöscht.
- Judge: meaning 1, language 0, invented-nothing 1, formatting 1 - The output is in German while the input text was in English.

## 15. Polish, `gemma4:e4b` on cpu (judge found a problem)
- Input: We shipped the update yesterday and there were some bugs but we fixed most of them already
- Instruction: Improve this text
- Output: Wir haben das Update gestern versendet, es gab einige Fehler, aber wir haben die meisten bereits behoben.
- Judge: meaning 1, language 0, invented-nothing 1, formatting 1 - The output is in German while the input text was in English.

## 16. Cleanup light, `gemma4:e2b` on cpu (judge passed it (check the judge))
- Input: Ähm also ich wollte dir kurz sagen dass das Meeting äh morgen um 10 Uhr ist
- Output: Ich wollte dir kurz sagen, dass das Meeting morgen um 10 Uhr ist.
- Judge: meaning 1, language 1, invented-nothing 1, formatting 1 - ok

## 17. Polish, `gemma4:e2b` on cpu (judge passed it (check the judge))
- Input: Das Produkt ist gut aber der Preis ist zu hoch und der Support war langsam
- Instruction: Formuliere das als sachliche Kritik
- Output: Das Produkt weist positive Aspekte auf, jedoch ist der Preis als zu hoch einzustufen, und die Reaktionszeit des Supports war unzureichend.
- Judge: meaning 1, language 1, invented-nothing 1, formatting 1 - ok

## 18. Cleanup medium, `gemma4:e4b` on cpu (judge passed it (check the judge))
- Input: Hallo Frau Becker ich ich melde mich wegen der Rechnung vom zwölften Oktober die ist noch nicht bei uns angekommen
- Output: Hallo Frau Becker, ich melde mich wegen der Rechnung vom 12. Oktober; die ist noch nicht bei uns angekommen.
- Judge: meaning 1, language 1, invented-nothing 1, formatting 1 - ok

## 19. Cleanup light, `gemma4:e4b` on gpu (judge passed it (check the judge))
- Input: So yesterday we talked to the client and they they want a first version by Friday
- Output: So yesterday we talked to the client and they want a first version by Friday.
- Judge: meaning 1, language 1, invented-nothing 1, formatting 1 - ok
