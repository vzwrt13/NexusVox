/* NexusVox Dashboard — what Pip says on Home: the one data file for his daily speech bubble */

// Pip shows at most one bubble a day (dashboard-pip-greeting.js picks it). He looks at three lists in this order:
//
//   1. PIP_MILESTONES - a goal reached that he has not mentioned before.
//   2. PIP_TIPS       - a free feature the user has never used, that he has not mentioned before.
//   3. PIP_STATS      - a number from this week, when nothing else is due.
//
// To add something, add one entry; no code changes. Rules for every text:
//   - one or two short sentences, English, plain;
//   - counts only: a text never contains, and the page never loads, anything that was dictated or transcribed;
//   - a tip is about a feature everyone has; it never mentions a paid plan;
//   - an `id` is kept for good, because the page remembers which ids Pip has said.

// `counter` is a key of what GET /api/pip/facts returns (all counts); the milestone is reached at `atLeast`.
// When several are reached at once they are said in list order, so keep each counter's goals smallest first.
const PIP_MILESTONES = [
  { id: "first-dictation", counter: "dictations", atLeast: 1, text: "Your first dictation is saved. That is how it starts." },
  { id: "first-meeting", counter: "meetings", atLeast: 1, text: "Your first meeting is recorded and transcribed. Nicely done." },
  { id: "first-file", counter: "uploads", atLeast: 1, text: "Your first file is transcribed. Drop in as many as you like." },
  { id: "words-1000", counter: "words", atLeast: 1000, text: "1,000 words dictated so far. Your fingers say thanks." },
  { id: "dictations-100", counter: "dictations", atLeast: 100, text: "100 dictations so far. You have found your rhythm." },
  { id: "words-10000", counter: "words", atLeast: 10000, text: "10,000 words dictated so far. That is a lot of typing you did not do." },
  { id: "words-50000", counter: "words", atLeast: 50000, text: "50,000 words dictated so far. Roughly a novel, all spoken." },
  { id: "words-100000", counter: "words", atLeast: 100000, text: "100,000 words dictated so far. What a milestone." },
];

// `unused` is a key of the same facts; the tip is for someone whose count is still 0 (a feature never used).
const PIP_TIPS = [
  { id: "tip-voice-commands", unused: "formatting_commands", text: "Say \"new paragraph\" (or \"neuer Absatz\") or \"new line\" while you dictate, and the text is formatted as you speak." },
  { id: "tip-own-words", unused: "dictionary_entries", text: "Add names and product terms under Settings > Vocabulary, and they are spelled your way from then on." },
  { id: "tip-file-upload", unused: "uploads", text: "Have a recording lying around? Drop an audio or video file on the Recordings tab and it turns into text." },
  { id: "tip-meetings", unused: "meetings", text: "Record a meeting from the Recordings tab and get a transcript of both sides." },
];

// The statistic. `{words}` is the dictated words this week, `{saved}` the typing time saved (for example "25 min")
// and `{wpm}` the typing speed behind it. The estimate is the one Home's "This week" card shows: the words typed at
// `typing_wpm` words a minute (40, from analytics_overview._TYPING_WPM, served with the facts), minus the time spent
// speaking. Both are said "about", because it is an estimate. A week without dictation has no statistic: Pip stays
// quiet. The variant of the day is picked by the day number, so a day keeps its text.
const PIP_STATS = [
  "{words} words dictated this week: about {saved} of typing saved, at {wpm} words a minute.",
  "This week you spoke {words} words instead of typing them: about {saved} saved at {wpm} words a minute.",
];

// How the bubble looks follows the kind of content (milestone, tip, stat), so the look is data too. Style "a" is the
// plain card with a small × in its corner; style "c" is the same card with a small label line above the text, which
// `PIP_LABELS` gives per kind (a kind without a label has none).
const PIP_BUBBLE_STYLES = { milestone: "c", tip: "a", stat: "a" };
const PIP_LABELS = { milestone: "Milestone" };
