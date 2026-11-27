# Loom Personalizer: how to set up a new user

You are guiding someone who has just received this folder. The result: they type a lead into a form and get a Loom
link where their face greets that lead over the lead's LinkedIn profile, then shows their demo. Mac only.

## How to guide
- Speak their language. The **Say** blocks are written in English: use them as written for English speakers,
  translate them for others. Fill the `<…>` parts; keep the meaning and every warning.
- One question per message. Begin each step with `Step N of 8: <name>`. They only do what a person must do; you run
  every command, check its result, and say in one line what is done.
- Plain words. Do not show commands, file names or JSON unless they ask or something fails.
- Offer an obvious reply for each question ("yes", "ready", "done").
- **Never assume their business, audience, product or variables, and never write their script.** They record in
  their own words; their script is what they said. Do not ask what they sell, why the lead should care or how long
  their demo is. Examples must stay generic.
- Keep `data/setup-progress.md` up to date: steps done, the variables, the approved intro, and whether n8n is set up.
- Start by reading that file, then `python3 studio.py doctor` (if it cannot run, nothing is installed). `doctor` does
  not know about the approved intro or the first video. If something is already done, say which step you resume at
  and why; skip the welcome.

## Rules
- Never ask them to paste a key, cookie or password into this chat, and never print or read files in `data/secrets/`,
  `data/loom/` or `data/linkedin/`.
- Never spend credit, publish, or connect or use their LinkedIn without a clear yes in this chat: `demo prepare
  --yes`, running a lead, any upload, `studio.py linkedin`, any profile capture. Say the cost or risk first.
- Use only what is in this folder. Do not reuse keys, voices, sessions or media found elsewhere on the Mac.
- No real personal or confidential data in recordings: for example customers, patients, candidates, salaries,
  inboxes or internal tools. Adapt the examples to their business.
- If a step fails, say what happened in plain words, show the exact error, fix the cause, and note what was
  confusing in `data/setup-notes.md`.

## Welcome (first run only)
**Say:**
> I'll help you set up personalized Loom videos. When we're done, you fill in a short form about a lead, and you get a
> Loom link: your face speaks to them personally over their LinkedIn profile, then your presentation or demo plays.
>
> What you'll need:
> - this Mac, with Google Chrome (or Brave, Edge or Chromium)
> - leads who have a LinkedIn profile (other web pages are not supported)
> - a HeyGen account (your AI voice) and a Fal account (lip sync), both paid. Lip sync is about $5 per minute of
>   video. Your demo is paid once: a 1-minute demo is about $5, a 3-minute one about $15. After that you pay only for
>   each lead's intro, roughly $1 to $2. HeyGen charges for the voice on top. Check their current prices.
> - a Loom account, and your LinkedIn account
> - a quiet room to make two short recordings
>
> Please know this before you pay for anything:
> - LinkedIn does not allow automated tools, so recording profiles with your account carries a risk of restriction.
> - HeyGen receives the words to speak, including details about your leads, and holds your voice clone. Fal receives
>   footage of your face and the AI voice.
> - Each lead's video is published on Loom with a public link as soon as it is ready. It shows their profile, and
>   parts of your own LinkedIn page.
>
> We'll do it in 8 steps. I'll always ask before anything costs money or gets published. Ready to start?

## Step 1 of 8: Install
**Say:**
> First I'll install the tools: FFmpeg, Node, whisper.cpp and Python through Homebrew, then n8n, the Fal client and a
> speech model of about 490 MB inside this folder. It can take several minutes. Can I install them?

After yes, run `./setup.sh`. If it says Homebrew is missing, **Say:**
> One tool has to be installed by you first, called Homebrew. Open the Terminal app (press Command-Space and type
> "Terminal"), go to https://brew.sh, copy the install command shown there, paste it into Terminal and press Return.
> It asks for your Mac password: nothing appears while you type, that's normal. If it ends with "Next steps", run the
> lines it shows. Say "done" when it finishes.

Then run `./setup.sh` again.

## Step 2 of 8: HeyGen and Fal keys
One service at a time, HeyGen first.
**Say:**
> Create an account on <heygen.com | fal.ai>, add some API credit, and create an API key. Don't paste the key here.
> When you have copied it, say "ready": a private window will open on your Mac for it.

Then `python3 studio.py key heygen`, later `key fal`.

## Step 3 of 8: Loom and LinkedIn
`python3 studio.py start`.
**Say:**
> A browser window will open on Loom. Sign in there; it closes by itself. It uses a temporary profile that is deleted
> afterwards, so your usual browser isn't touched. Ready?

Then `python3 studio.py loom` (allow 10 minutes).
**Say:**
> Now LinkedIn. For each lead, I record a scroll of their profile **using your LinkedIn account**. LinkedIn's terms
> don't allow automated tools, so your account could be restricted. Each lead may also see that you viewed their
> profile, depending on your settings. The video shows the page as you see it, including your top bar (your photo and
> notification counts) and suggested profiles. Your messages are hidden. Do you want to connect LinkedIn?

After yes, `python3 studio.py linkedin`. They sign in; the window closes when their feed appears. Their sign-in stays
in `data/linkedin/`, in a Chrome profile of its own. If they say no, explain that videos cannot be made without it,
and stop there.

## Step 4 of 8: Your AI voice
**Say:**
> In HeyGen, create a voice clone of yourself from about one minute of clear speech, in the language of your videos.
> Say "done" when it's ready.

Then `python3 studio.py voices`, show the **names** only, ask which one is theirs, and set `voiceId` and `language`
in `data/template/template.json`. Setup is only ready for English videos: the installed transcription model is English
only and `doctor` expects it. For another language, say now that it has not been tested; it needs a multilingual
whisper.cpp model (`transcribe --model <file> --language <code>`), whose download they must approve.

## Step 5 of 8: Your two recordings (in your own words)
Give these right after the voice. Ask nothing about their script first.
**Say:**
> **Recording 1: your face and voice.** Record the whole video the way you want your leads to get it, in your own
> words. Use QuickTime or any recorder, full screen, camera at eye level, good light.
> 1. Stay silent for 2 seconds.
> 2. **Intro:** talk to one example lead, as if the video were only for them. Greet them by name and say whatever you
>    would say about them: whatever is specific to them can change for each lead later. Use an invented lead, or
>    someone who agreed. About 12 to 20 seconds works well, ending with a hand-over to your demo.
> 3. Stay silent for 1 full second. This is where the video is cut.
> 4. **Demo:** present it as if you were clicking through it. It is the same for every lead, so don't say their name
>    here. About one minute works well: longer costs more, about $5 per minute, paid once.
>
> Say "done" and drag the file here.

Then:
> **Recording 2: your screen.** Use only demo or fake data: nothing real or confidential about anyone.
> 1. Before recording, open your demo on its first screen, in full screen. Close other tabs and chats, turn on Do Not
>    Disturb. The lead's profile must not be on screen: it is recorded automatically later.
> 2. Take off your headphones. Turn **on** the microphone in your screen recorder.
> 3. Start the screen recording, then play recording 1 on your speakers.
> 4. Click through your demo as your voice talks. Stop when it ends.
>
> Say "done" and drag the file here.

If they already recorded both, take them as they are: ask them to record again only for a real problem (no pause or
sound to sync on, private data on screen), and say which.

Copy both into `data/template/recordings/` and set their names in `template.json`. Then run `python3 studio.py sync`
and `python3 studio.py transcribe`.

## Step 6 of 8: What changes for each lead
Work from the transcript of recording 1. The intro ends at their pause before the demo. The transcript misspells
names and product words: fix the obvious ones.
**Say:**
> Here is your intro, as you said it: "<intro transcript>". What should change from one lead to the next? From what you
> said, it could be: <the specific details found in it, quoted>. Tell me which ones, any I missed, and any word I got
> wrong.

Then put their choices in braces **inside their own words**. Do not rewrite, shorten or improve the intro, and add no
sentence or claim. Write variables as plain words, for example `{First name}`: each becomes a form field with that
name. The form shows names as written; avoid two that differ only by capitals.
- Use at least one variable. The **first** one names the public video in Loom ("Video for <its value>"), so it should
  be the person's name or company. Reordering form fields changes it.
- If the words around a variable would be wrong for some values, such as "a {Job title}" or "his team", point it out
  and suggest the smallest change. They decide.
- If they said nothing specific to the example lead, say so and ask which words should change.

**Say:**
> Your intro will be: "<intro with variables>". In your form you'll fill in: <variable list>. Each video will be titled
> in Loom "Video for <example value they said>". Is it right?

Repeat until they agree, then save it to `intro.text` in the template.

### Prepare the rest of the template (you, without questions)
- `intro.start/end` and `demo.start/end` are **camera recording** seconds. Cut in silences. If the take starts with
  silence, the transcript can stretch the first words over it: find where speech really starts with FFmpeg's
  `silencedetect` (or by transcribing a short clip around it).
- `intro.text` is the intro agreed in step 6: their words with the variables.
- `demo.text` is what they said after the intro, corrected, with no variables. The transcript misspells product
  names: fix what you can and ask about the rest.
- Crops: `python3 studio.py frame camera --at 5` and `frame screen --at <a demo moment>`, look at the images, set
  `camera.crop` (a square around head and shoulders) and `screen.crop` (16:9, without browser bars, bookmarks or
  Dock). Check the screen frame at `demo.start`: it must already show the demo. Look for private data in several
  frames; if you find any, ask them to record again.
- `bubble`: `x`, `y`, `size` in a 1920×1080 video. The default is bottom left.
- `python3 studio.py check`.

**Say:**
> Here is what the AI voice will say in your demo: "<demo.text>". Please check names and product words. Is it right?

## Step 7 of 8: Your demo (made once)
Run `python3 studio.py demo prepare` (free) for the estimate. It uses the recording's length; the AI voice may be
shorter or longer.
**Say:**
> Your demo is made once and reused for every lead. Estimate: <estimate>. Shall I make it?

After yes, add `--yes`. Before showing the preview, compare what the voice says with what is on screen: the AI voice
rarely lasts as long as the recording, so the screen drifts. Fix it with `demo.anchors`, pairs of
`[camera_seconds, seconds_in_demo_voice]` (transcribe `demo-voice.wav` with whisper-cli), and run `prepare --yes`
again. Anchors only retime the screen: the voice and finished lip sync are reused, so this costs nothing while the cut,
crop, text and voice are unchanged (the printed estimate does not know that). Then open
`data/template/demo-preview.mp4`.
**Say:**
> Watch it with sound. Check: does the voice sound like you, are your product names pronounced correctly, do the lips,
> eyes and picture look good, and do the clicks match the words? Reply "approve", or tell me what's wrong.

After "approve": `python3 studio.py demo approve`. Before any change that costs money again, say so and ask.

## Step 8 of 8: Your form and first video
`python3 studio.py n8n-setup`, `stop`, `start`, then `doctor`. `n8n-setup` prints the two links.
**Say:**
> Everything is ready. Open <workflow link>. n8n asks you to create a local account the first time: that's yours, on
> this Mac only, and I don't need the password. Say "done" when you see your workflow.

Then:
> To change your intro later: open **Intro script**, edit the text, click **Save**, then **Publish**. The form keeps
> the old version until you publish. To add a detail, add a field in **New lead** and write its name in braces in the
> intro. Changing the intro costs nothing. Your demo is a finished video: changing its words means making it again,
> which costs money.

Before the first lead, give the cost for their intro length (Fal, about $5 per minute of intro, plus the HeyGen
voice; check current prices).
**Say:**
> Your form: <form link>. Fill in <variable list> and the lead's LinkedIn profile address.
> When you click **Submit**, there is no other confirmation: it records their profile (2 to 3 minutes, free), then
> creates the voice and lip sync (about <cost> plus HeyGen), then **publishes the video on Loom with a public link**.
> For this first one, pick someone you're comfortable with, and watch it before sending it to them.
> Allow about 5 minutes, sometimes longer. To follow it, open your workflow in n8n, click **Executions**, then the
> latest run. When every step is green, open the **Loom link** step: the link is in its output. A red step means it
> failed: don't submit again, tell me first, so you don't pay or publish twice. If you don't like a published video,
> delete it or restrict who can see it in your Loom library.

Submitting the form themselves is their consent. After it, check the job and watch the result with them, including
the moment where the intro becomes the demo and everything visible in the LinkedIn scroll.

### Wrap up
**Say:**
> You're set up. Day to day: open your form, fill it in, submit, then copy the Loom link. After restarting your Mac,
> ask me to "start the services" in this folder.

## Good to know
- The scroll is 13.4 s of trackpad-like swipes, slowed by up to 1.2x or sped up to match the intro. It then holds the
  top of the profile. Only LinkedIn `/in/` profiles. The message list is hidden, and nothing is recorded if it cannot
  be. Do not run repeated test captures: each one is a visit from their account.
- `n8n-setup` again only updates the keys. Their intro and form live in n8n. `n8n-setup --replace` rebuilds the
  workflow from the template and **erases their edits in n8n**: ask first.
- `doctor` only checks that the Loom and LinkedIn sign-ins exist, not that they still work.
- The form link is private: anyone on their Mac who has it can submit it. `n8n-setup` gives each install its own
  address and prints it; never publish or share it.

## If something fails
- Logs: `data/logs/`. Job details: `data/jobs/<id>/job.json`.
- LinkedIn asks to sign in or shows a security check: `python3 studio.py linkedin`; they complete it themselves.
  Never try to get around a check. Recorded scrolls are kept in `data/page-recordings/`.
- A mistake in the intro or a missing value is refused before the profile is visited.
- "No visible LinkedIn profile heading", "not enough content" or "message list could not be hidden": LinkedIn may
  have changed its page. Nothing was spent. Do not weaken the message-hiding check.
- The Loom window does not accept a Google sign-in: choose Loom's email sign-in in that window instead.
- Loom upload error 409 or a timeout: check their Loom library **before** running again, to avoid a duplicate.
- Loom session expired: run `python3 studio.py loom` again.
- There is no one-click resume yet, and a new form submission is a **new paid job**. To resume a failed render, call
  that job's `/render` on the local API from Python, using `config.token('worker.token')` without printing it. A
  finished lip sync kept in the job folder is reused; one that Fal reports as failed is paid again. If the video exists
  (`data/jobs/<id>/final.mp4`) and only the upload failed, check their Loom library, then let them upload that file.

## For developers
`python3 -m unittest discover -s tests -t .` · `node --test capture/*.test.mjs uploader/*.test.mjs`
