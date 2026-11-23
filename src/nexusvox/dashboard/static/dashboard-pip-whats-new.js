/* NexusVox Dashboard — what Pip introduces after an update: the one data file for his "what's new" popup */

// After an update to a version listed here, Pip shows a short popup on Home, one step per feature (dashboard-pip-tour.js
// shows it, once per version). To introduce features in a release, add one entry for its version; no code changes.
// A version that is not listed here shows nothing. The file ships with the app and the popup never asks the network.
//
//   "1.1.0": [
//     { title: "Spoken formatting", text: "Say \"new paragraph\" while you dictate.", target: "settings" },
//   ],
//
//   - the key is the app version exactly as the package reports it (`nexusvox.__version__`, "1.1.0", without a "v");
//   - `title` is a few words, `text` one or two short sentences; English, plain, never about a paid plan;
//   - `target` is optional: the dashboard page the "Show me" link opens, a tab name ("recordings") or a tab with its
//     page ("settings/vocabulary", "analytics/week"). An entry without a target has no "Show me" link.
//   - pick the features worth a mention, not every change: three or four steps are plenty.
const PIP_WHATS_NEW = {
  "1.0.1": [
    { title: "Model download asks first", text: "The speech model downloads only after you click Download, so nothing big arrives unannounced.", target: "settings/models" },
    { title: "Easier exports", text: "The Export menu on a recording now lists each format as its own row.", target: "recordings" },
    { title: "Speakers in meetings", text: "Each speaker in a meeting transcript now has a row of their own.", target: "recordings" },
    { title: "Help and diagnostics", text: "The Help link opens the online guide. Copy diagnostics under Settings > Privacy gathers what a bug report needs and sends nothing.", target: "settings/privacy" },
    { title: "Hello from me", text: "I now greet you on Home with a small note now and then. You can turn me off in Settings if you prefer.", target: "home" },
  ],
};
