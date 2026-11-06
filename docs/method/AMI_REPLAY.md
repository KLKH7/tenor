# AMI replay protocol

This page is TENOR's protocol for replaying public AMI Meeting Corpus recordings into meeting products and scoring them against AMI's human transcripts and annotations: corpus facts, licence, protocol, meeting selection and risks.

**Status (2026-11-05):** 7 meetings (IS1008a–d, IB4003, EN2002b, ES2006a) replayed into Granola and Wispr Flow (decisions and pilot findings in §9). The design below was written on 2026-11-04, before any playback; decisions taken since are in §9 and §10.

**Original status (2026-11-04): design only.** Nothing had been downloaded, played or run. Spend so far: $0. Facts marked ✓ were read on the official pages listed in §8. Everything else is a proposal or an ASSUMED behaviour to check in a pilot.

## In short

- **Why.** Headline findings must come from real meetings. Kian's real meetings are private and few. AMI is real speech with human ground truth, licensed CC BY 4.0, so anyone can rerun exactly what we ran.
- **What it unlocks.** A four-meeting series by the same team gives cross-meeting questions (decisions, changes, owners) with answers taken from human annotations, not from an LLM. It also gives a new metric for free: transcription accuracy (WER, word error rate) against AMI's manual transcript.
- **Cost.** About 2 h 40 min of real-time playback (6 meetings), about 310 MiB of downloads, and about $2 of judge spend per full scoring run. Kian runs the playback.
- **Main caveats.** The scenario meetings are role-play from 2005. Everything reaches the products as one mixed channel. AMI is public, so models may have seen it. §7 covers each risk and how it is controlled.

## 1. Facts about AMI (verified at source)

| Fact | Value | Source |
|---|---|---|
| **Licence** ✓ | CC BY 4.0, standard legal code, no NC or ND clause, no added terms. The licence page: "The AMI corpus and its annotations are released under the Creative Commons Attribution 4.0" | license.shtml |
| **Licence scope** ✓ | The home and download pages are more cautious: "all of the signals and transcription, and some of the annotations". The two annotation zips were relicensed to CC BY 4.0 on 10 Apr 2017 | home, download |
| **Size** ✓ | 100 hours. About two-thirds is role-play scenario meetings, the rest naturally occurring meetings | home |
| **Language** ✓ | English, "mostly non-native speakers" | overview |
| **Sample rate** ✓ | 16 kHz WAV, downsampled from 48 kHz | signals.shtml |
| **Files** ✓ | `amicorpus/<ID>/audio/<ID>.<stream>.wav`. The streams are `Mix-Headset` (all headsets mixed, mono), `Mix-Lapel`, `Headset-0..3`, `Lapel-0..3`, `Array1-01..08`, `Array2-01..08` (far-field table arrays; Idiap's Array2 has 4 channels) | mirror directory listings |
| **Annotations** ✓ | `ami_public_manual_1.6.2.zip`, 22 MB, NXT XML. It contains per-speaker words with forced-alignment timings, dialogue acts, topic segments, extractive summaries (about 10% of the transcript, linked to the abstract's sentences), abstractive summaries (about 200 words, under ABSTRACT / DECISIONS / PROBLEMS / ACTIONS), decision points (a subset of meetings), named entities, and `meetings.xml` (speaker letter → role → audio channel) | download, annotation.shtml |
| **Coverage** ✓ | Every meeting has a manual transcript. Nearly every scenario meeting has summaries, dialogue acts and topics. EN and IN (natural) meetings have the transcript only | annotationpresent.shtml |
| **Partitions** ✓ | Standard train / dev / test splits exist. IS1008 is dev in all three partitions, IB4003 is dev, EN2002 is test | datasets.shtml |

**Meeting types** ✓ (scenariomeetings, meetingids, nonscenariomeetings):

| Type | IDs | What it is |
|---|---|---|
| **Scenario** | ES2002–2016, IS1000–1009, TS3003–3012, each with sessions a–d (a few missing) | 4 people play a design team: project manager, marketing expert, user-interface designer, industrial designer. They design a TV remote in four meetings recorded on one day: kick-off, functional, conceptual and detailed design. Participants were "neither professionally trained for design work nor experienced in their role". About 11–56 min each |
| **IB** | IB4001–4011 | Staged too: "a fictitious scenario of their own: these are not naturally occurring meetings" |
| **EN, IN** | EN2001–2009, IN1001–1016 | Real meetings that "would have happened whether they were being recorded or not" (students, research colleagues). Transcript only |

### Licence: what we may do, and the attribution

- **Playing the audio into commercial products is allowed.** CC BY 4.0 permits any use, including commercial use, reproduction and adaptation, with attribution. Sending the audio to Granola or Wispr is a reproduction; their transcripts and notes are adaptations.
- **Publishing derived scores is allowed.** Scores, labels and question sets built from AMI annotations are adaptations, and CC BY only asks for attribution plus a note of what changed.
- **Two things the licence doesn't cover.** CC BY §2(b) excludes privacy and publicity rights. The participants signed a consent form (`ami_consent_form_081004.pdf`) that we have **not read**. Kian should read it before we start (open question 2). A precedent exists: [msallese31/ai-note-taker-test-2026](https://github.com/msallese31/ai-note-taker-test-2026) did exactly this with Granola, Otter, Fireflies and Scribbl, and published the outputs.
- **Product terms are a separate question.** Whether we may publish Granola's or Wispr's raw notes depends on their terms, not on AMI's. Until checked, we commit per-claim labels and aggregates only (open question 3).

**Attribution text** (for the README, the results page and each published file built from AMI):

> Uses the AMI Meeting Corpus (University of Edinburgh and AMI consortium partners), licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), https://groups.inf.ed.ac.uk/ami/corpus/. Changes: the audio was played in real time into third-party meeting products, and questions were derived mechanically from the manual annotations. Citation: J. Carletta et al., "The AMI Meeting Corpus: A Pre-announcement", MLMI 2005, LNCS 3869, pp. 28–39.

## 2. Why it fits TENOR

| Need | What AMI gives |
|---|---|
| **Real speech** | Real people really talking, with overlap, hesitation and accents. Not generated text |
| **Human ground truth** | Who said each word and when; decisions, actions and problems written by annotators; links from each summary sentence to the dialogue acts that support it |
| **Cross-meeting memory** | One team meets four times. Decisions made in meeting b are revisited in c and d. That gives change and commitment questions, not just questions about one meeting |
| **Shareable and reproducible** | Anyone can download the same files, play them into any product and score them with our code. Unlike Kian's meetings, per-question results can be published |
| **Comparable with systems** | The same meetings, loaded from the *human* transcript, can run through the public track (the archived Concentrate memory system, Mem0, plain RAG). That puts products and systems on the same questions for the first time |

## 3. Protocol: playing a meeting into both products

**The setup** (one Mac, both apps running):

| Step | What to do | Why |
|---|---|---|
| **Dedicated accounts** | Use a fresh Granola account and a fresh Wispr account that hold only AMI meetings | Keeps Kian's private meetings out of retrieval and out of the cross-meeting bleed check, and makes outputs publishable |
| **Calendar event** | For each playback, create an event titled `AMI IS1008a` (etc.) at that time, with no attendees. Both apps read the calendar | Both products get the same title and start time, so `tenor pair` matches them, and the title names the AMI source |
| **Audio file** | `<ID>.Mix-Headset.wav`, unchanged: no conversion, no MP3 | The cleanest mono mix. Far-field array channels would add room reverb, a second variable we don't want in v1 |
| **Player** | `afplay <file>` (built into macOS) at 1.0× speed | Real time, no player chrome, nothing to misclick |
| **Microphone** | Input volume at 0, or a muted mic, in the room | Otherwise the speaker output re-enters the mic as a duplicate "Me" track |
| **Output** | Normal volume through headphones, or volume 0 if the pilot shows capture is pre-volume. The precedent ran Granola with output at 0 | Both apps capture system audio digitally (Screen & System Audio Recording permission), not through the air |
| **Quiet Mac** | Do Not Disturb on, nothing else playing, sleep disabled | System sounds would be captured as speech |
| **Start/stop** | Start both recorders, then `afplay`. Stop both 30 s after the file ends | |

**Pilot first (≈ 5 min).** Play 2 minutes of one meeting and check that **both** apps transcribe at the same time, and with which output volume. Two apps capturing system audio at once is ASSUMED to work; nobody has checked it here.

**One channel, many speakers: is that fair?** Yes, and it is realistic.

- **What happens.** Granola labels audio by capture channel: mic = Me, system audio = Them. All four AMI voices arrive on system audio, so Granola's transcript is all "Them" with no split between speakers (desktop diarisation isn't exposed, per [SCHEMA_NOTES.md](../engineering/SCHEMA_NOTES.md)). Wispr diarises system audio into Speaker 1, 2, … and names speakers afterwards from the calendar invite and names said aloud (Wispr's own pages).
- **Why it's fair.** It is exactly what a product faces when the owner listens silently to a four-person call: every remote voice arrives mixed on one channel. It isn't fair to expect *names*, because AMI people rarely say their names. So who-said questions ask for **roles**, which people do say ("as marketing expert, I…"), especially in the kick-off meeting.
- **How it's reported.** Who-said results are reported as their own slice, never in a headline. Granola answering "unknown" on a role question is a correct reading of its capture model, not a failure to hide.
- **Variant for later, not v1.** Route the project manager's own headset channel to the mic through a virtual device, and the other three to system audio. Granola then gets a true Me/Them split, as in a real call where Kian is the PM. It needs a local re-mix and a third-party audio driver, so it waits until v1 shows the track is worth it.

**Pairing with the AMI source.**
1. **Product ↔ product:** the existing `tenor pair` (±10 min gate, transcript 3-gram containment), with the shared calendar title as the third signal.
2. **Product ↔ AMI:** the calendar title gives the AMI ID. A check confirms it: the product transcript's word 3-gram containment against the AMI manual transcript must be at least 0.3, and highest for the claimed ID. A mismatch blocks scoring.
3. **Reference loader:** a new `tenor.corpora.ami` reads the NXT files into a reference transcript (speaker letter, role, words with times) plus the annotations. It is not a `Source` (it is not a Granola-shaped product). For the public track it can also emit `MeetingNote`s with roles as speaker names.

**Time.** Real-time playback, plus about 5 minutes of setup and processing wait per meeting. The proposed set (§5) is 2 h 39 min of audio, so about 3 h 10 min of wall-clock time. Kian only needs to be present at the start and end of each meeting. Play the series in order (a → d), on one day if possible: AMI's four sessions were recorded on one day too.

## 4. Tracks it unlocks

| Track | Task on AMI | Ground truth | Metric | Spend |
|---|---|---|---|---|
| **Transcription accuracy** (new) | Compare each product's transcript (via MCP) with the AMI manual words | AMI `words.xml`, all speakers merged by start time | WER after normalisation (lower case, no punctuation, AMI vocal-sound and filler tokens removed on both sides, numbers spelt out); also content-word recall, which doesn't depend on word order across overlapping speakers | $0 |
| **Notes faithfulness** | The existing pipeline, with `paired_cases` using the **AMI manual transcript** as the one shared reference | Human transcript with role labels, so wrong-owner claims can now be caught. Granola's own channel transcript can't catch them | supported / inferred / unsupported / contradicted by claim type; plus **coverage**: share of abstractive-summary sentences the notes state (the precedent's answer-key method) | ≈ $0.4 per product for 6 meetings ($0.26 for 4 real meetings so far) |
| **Question answering** | Questions from §6, asked to Granola's agent and to the standard outside agent on both products | Decisions, actions, problems, decision points, summary→dialogue-act links, roles | accuracy with 95% CI, abstention on unanswerable questions | ≈ $0.5 judge spend for ~60 questions × 3 rows |
| **Retention** | The same questions with the outside agent allowed notes only, vs notes plus transcript | as above | loss per question type when transcripts are unavailable | ≈ $0.3 |
| **Agent interface** | The same questions; score the payloads | as above | the four answer types, evidence granularity, stability across repeats, latency | ≈ $0.3 |

Every eval run states its estimate first and asks before going over $2, as usual. A full pass of all five tracks should come to about $2, so each run gets its own quote first.

**What's newly possible here:** per-question outputs on real speech can be committed, so Granola's engineers can open any failure and listen to the exact second of audio behind it.

## 5. Selection: 6 meetings, 2 h 39 min

| Meeting | Length ✓ | Type | Why |
|---|---|---|---|
| **IS1008a** | 15:31 | scenario, kick-off | Roles are introduced here; later who-said questions depend on remembering them |
| **IS1008b** | 29:24 | scenario, functional design | |
| **IS1008c** | 25:46 | scenario, conceptual design | |
| **IS1008d** | 24:30 | scenario, detailed design | Also in the precedent, so it is a direct replication check |
| **IB4003** | 33:43 | staged, non-scenario (an office move) | A different task and team, with abstractive summaries. Also in the precedent |
| **EN2002b** | 29:47 | real, natural meeting | The realism check: transcript only, so it feeds WER and notes faithfulness, not QA |

**Why IS1008 over the other series:**
- **The fullest annotations.** It has decision points in all four meetings, plus summaries, dialogue acts and topic segments. Few series have all of these (ES2008 has no decision points; ES2002 has them in a and d only).
- **Clean audio.** No meeting-specific entries on dataproblems.shtml. ES2002 has a misworn headset and dropouts, and ES2008c has a dropout.
- **Held out from training.** IS1008 and IB4003 are in AMI's dev set and EN2002 is in its test set, in every standard partition. Speech models trained "on AMI" normally train on the train split only, which lowers (but doesn't remove) contamination (§7).
- **Short.** 95 minutes for the whole series.

**Stretch, if v1 is useful:** TS3005 a–d (2 h 28 min, decision points in all four, a different site) as a second series. That doubles the cross-meeting questions.

## 6. Question construction: from annotations, no LLM drafting

Questions are produced by a deterministic script (`tenor ami-questions --seed`) from the annotation files. No LLM writes or rewrites a question. Kian's only role is **keep or drop** on a printed list; he never edits wording.

| Template | Built from | Example shape | Answer key | Est. n |
|---|---|---|---|---|
| **T1 decisions in a meeting** | DECISIONS section of the abstractive summary | "What did the team decide in the conceptual design meeting of team IS1008?" | the list of decision sentences, scored by item recall | 5 |
| **T2 who proposed it** | decision sentence → linked dialogue acts → speaker letter → role from `meetings.xml` | "Which role first proposed: '<decision sentence>'?" | the role holding at least 60% of the linked dialogue acts, else the question is dropped | ~15 |
| **T3 action owner** | ACTIONS sentences that name a role (a closed set of 4 role names, matched by regex) | "Which role was asked to: '<sentence with the role replaced by [someone]>'?" | the role | ~10 |
| **T4 when was it decided** | each decision sentence plus the meeting it came from | "In which of team IS1008's meetings was it decided that '<decision>'?" | the meeting phase | ~15 |
| **T5 what changed** | decisions on the same AMI topic label (topics come from a closed default list) in two different sessions | "How did the decision on <topic> change between the functional and the detailed design meeting?" | both decision sentences; Kian keeps only real changes | ~4 |
| **T6 problems raised** | PROBLEMS section | "What problems were raised in meeting <phase>?" | item recall | 5 |
| **T7 unanswerable** | a decision from another series (TS3005) on a topic IS1008 never discusses | "What did team IS1008 decide about <topic>?" | must abstain | ~5 |

- **Estimate: about 60 questions** (range 40–70) from IS1008 + IB4003. That has to be counted from the files after download. At n = 60, a 95% CI on accuracy is about ±12 points; the TS3005 stretch would double n.
- **Weakness:** T2 and T4 quote the annotator's sentence, which gives away wording. This is acceptable for who and when questions, where the answer is a role or a phase, not the wording itself.
- **Split:** a seeded 50/50 dev/test split, stratified by template. Judge prompts and the outside agent's prompt are tuned on dev only.
- **Judge:** the existing `judge.v1`. Items with list answers (T1, T6) are scored per item, as the precedent did.

## 7. Risks and contamination

| Risk | How bad | What we do |
|---|---|---|
| **Products may learn nothing special.** A fresh account with 6 meetings and no attendees has no history, contacts or calendar context to use | Medium. It tests memory over a small, clean account, not a lived-in one | Say so in every result. AMI complements Kian's real meetings; it doesn't replace them |
| **Realism.** 2005 recordings, role-play, a toy design task, non-native speakers, 16 kHz audio, no screen share | Medium for the scenario meetings | EN2002b is a real natural meeting, kept as the check. Report scenario and natural meetings separately |
| **Audio path.** Products are tuned for live call audio, not a corpus mix | Low | The pilot checks capture. The precedent ran Granola this way and got normal-looking notes |
| **Single channel.** Granola gets no speaker split | Real, but it is the product's actual behaviour | Who-said is its own slice (§3) |
| **ASR contamination.** AMI is a standard ASR benchmark, and product speech vendors may have trained or tuned on it | Medium for WER | Use dev/test meetings only. Report WER as a *best case* for the products, never as a claim about real-world accuracy. Do not compare it with WERs on private meetings |
| **LLM contamination.** AMI transcripts and summaries have been public for about 20 years and sit inside summarisation sets (e.g. QMSum), so the models behind the products and our judge may know them | Medium for QA and summary coverage | **Closed-book control:** ask every question to the same model with no meeting access. Report its score, and flag every question it gets right. Headline QA numbers use only questions the closed-book model fails |
| **Consent scope.** The consent form isn't read; CC BY doesn't license privacy rights | Low (the data has been redistributed publicly for 20 years and the precedent used it this way) but unchecked | Kian reads the form before playback (open question 2) |
| **ICSI as an alternative** | | See below |

### ICSI: assessed, kept for later

| | AMI | ICSI |
|---|---|---|
| Licence | CC BY 4.0 ✓ | CC BY 4.0 ✓ (the home page says "some of the annotations") |
| Realism | two-thirds role-play | **real research group meetings** (75 meetings, about 70 h) ✓ |
| Series | 4 meetings per team, one day | **long real series**: Bmr 29, Bro 23, Bed 15 meetings ✓ |
| Length | 11–56 min | about 1 h each (ASSUMED from the 70 h / 75 split) |
| Audio | headset mix, lapel, arrays | headset mix (≈120 MB) and individual headsets only on the download page ✓ |
| Decisions / actions | annotated in abstractive summaries and decision points ✓ | not stated on the pages; summaries sit in a third-party "contributed" set ✓ |

**Verdict:** AMI for v1, because its decision and action annotations make mechanical questions possible. ICSI is the better test of long-horizon memory over a real recurring team. Three consecutive Bed meetings (about 3 h) would be the natural v2, once we know which ICSI annotations carry decisions.

## 8. Exact files to download

**Downloaded 2026-11-04 with Kian's approval.** Checksums: [`data/ami/MANIFEST.json`](../../data/ami/MANIFEST.json). All 6 WAVs verified as 16 kHz, 16-bit mono PCM; the zip passes an integrity test. All files are CC BY 4.0 (AMI licence page). Sizes marked ≈ are computed from the verified lengths at 16 kHz, 16-bit mono (32 kB/s); they match the two listings checked (IB4003 62M, ES2008a 32M).

Base: `https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/`

| # | File | URL | Size | Licence |
|---|---|---|---|---|
| 1 | `ami_public_manual_1.6.2.zip` | https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip | 22 MB (listed) | CC BY 4.0 |
| 2 | `IS1008a.Mix-Headset.wav` | `<base>IS1008a/audio/IS1008a.Mix-Headset.wav` | ≈ 28 MiB | CC BY 4.0 |
| 3 | `IS1008b.Mix-Headset.wav` | `<base>IS1008b/audio/IS1008b.Mix-Headset.wav` | ≈ 54 MiB | CC BY 4.0 |
| 4 | `IS1008c.Mix-Headset.wav` | `<base>IS1008c/audio/IS1008c.Mix-Headset.wav` | ≈ 47 MiB | CC BY 4.0 |
| 5 | `IS1008d.Mix-Headset.wav` | `<base>IS1008d/audio/IS1008d.Mix-Headset.wav` | ≈ 45 MiB | CC BY 4.0 |
| 6 | `IB4003.Mix-Headset.wav` | `<base>IB4003/audio/IB4003.Mix-Headset.wav` | 62 MiB (listed) | CC BY 4.0 |
| 7 | `EN2002b.Mix-Headset.wav` | `<base>EN2002b/audio/EN2002b.Mix-Headset.wav` | ≈ 55 MiB | CC BY 4.0 |
| | **Total** | | **≈ 310 MiB** | |

- **Optional, to read first:** the consent form `ami_consent_form_081004.pdf`, linked from https://groups.inf.ed.ac.uk/ami/corpus/ethicsandconsent.shtml (exact URL and size not checked).
- **Not needed for v1:** individual headsets (the PM-on-mic variant, about 4 × the mix size per meeting) and TS3005 (stretch, about 270 MiB).
- **Where they go:** `data/ami/` (gitignored, so audio is never committed), with SHA-256 checksums recorded in a committed `data/ami/MANIFEST.json`. Each file goes in its own directory and is read only by our scripts.

**Pages read for this doc** (all on `groups.inf.ed.ac.uk/ami/`, HTTP 200, 2026-11-04): `corpus/` (home), `license.shtml`, `download/`, `overview.shtml`, `signals.shtml`, `annotation.shtml`, `annotationpresent.shtml`, `meetingids.shtml`, `scenariomeetings.shtml`, `nonscenariomeetings.shtml`, `meetingrooms.shtml`, `datasets.shtml`, `dataproblems.shtml`, `ethicsandconsent.shtml`, the mirror listings for ES2008a, IB4003 and EN2002a, and `icsi/` (home, licence, download). Also the precedent's README, PROTOCOL, RESULTS and plans, and Wispr's Notetaker help pages.

## 9. Decisions (Kian, 2026-11-04)

| Question | Decision |
|---|---|
| Go / no-go and download | **Go.** The 7 files in §8 are downloaded |
| Dedicated accounts | **Yes:** fresh Granola and Wispr accounts holding only AMI meetings |
| Publishing product output | **Scores only.** Raw Granola and Wispr notes on AMI stay local and are never committed; we publish labels and aggregates |
| Publishing product output (revised 2026-11-05) | **Short quoted examples allowed (option b):** up to a sentence of a product's AMI output per example, shown beside the AMI source (CC BY 4.0), for criticism and analysis. Full product outputs stay local |

| Accounts (revised) | Granola only accepts work accounts, so both products use Kian's **main** accounts. Granola questions are scoped to the AMI notes with `document_ids`; our agent gets only AMI notes from Wispr. AMI notes are deleted after the run |
| Personal vocabulary | **Off for scored runs.** The pilot showed Wispr writing the account holder's name for an AMI speaker. Back up and clear the personal dictionary (both products, where one exists) before recording; restore after. Voice profile and account name can't be removed: a stated threat to validity |
| Room isolation | Speakers muted: products capture system audio digitally, the room stays silent. Granola mic turns are dropped and counted; Wispr has no capture channel, so it needs a silent room |

### Conditions and threats found in the pilot

**Silent replay (no typed notes).** Nobody typed during the AMI playbacks, so Granola wrote notes from the transcript alone: its least favourable mode, since its notes blend the user's typing with the transcript ("note quality depends on your participation", a 2026 review). Every AMI notes result is a lower bound for an engaged user. Planned test: replay the same meeting silent vs. with a few typed anchor notes, and compare faithfulness and completeness.


| Issue | What we saw | Mitigation |
|---|---|---|
| **Playback ≠ a real call** | AMI goes straight in as system audio: clean, uncompressed 16 kHz, but no calendar invite, attendee names or meeting-app context, and in-room crosstalk among mostly non-native speakers | Same input for both products, so the **comparison** is fair; **absolute** numbers describe this condition only, not typical calls. Report as a threat to validity |
| **Mic auto-gain** | The Mac's input volume rises on its own now and then (another app's automatic gain control), so a "muted" mic stops being muted | Speakers stay muted, so the mic can only hear the room. Granola mic turns are dropped and counted (`mic_utterances`). Wispr has no capture channel, so log input volume during every playback and flag any recording where it rose above 0 while the room wasn't silent |

**Call condition (planned, after the clean run).** A second condition closer to real use: start a Google Meet with the laptop (running both products) as one participant and a second device (e.g. a phone) as the other, playing the AMI file into the call. This adds call compression, packet loss, noise suppression and meeting-app context. Same 6 meetings, scored the same way; reported **alongside** the direct condition, never mixed. The gap between them measures what the call path costs or adds.

**Pilot (2026-11-05):** IS1008a recorded once with personal settings on, to test the pipeline. Re-recorded under the clean protocol before any result is reported.

| Pilot, IS1008a only (n = 1 meeting, not a result) | WER | Content-word recall | Speakers found |
|---|---|---|---|
| Granola (mic turns dropped: 0) | 10.0% | 94.2% | 0 / 4 (no diarisation in its MCP output) |
| Wispr Flow (all turns kept) | 9.0% | 95.8% | 3 / 4 |

One meeting, one recording each, personal dictionaries on, and one stray room line in Wispr: a one-point gap is a **tie**, not a ranking. What the pilot does show: the pipeline works end to end, and Granola's MCP transcript format changed overnight (see SCHEMA_NOTES).

**Still open:** reading the participants' consent form; free-plan limits on the fresh accounts; Wispr sign-in (see [PRODUCTS.md](HARNESS.md)).

## 10. Tooling (built 2026-11-04, $0)

| Command | What it does |
|---|---|
| `tenor ami status [--db data/ami_granola.db]` | Ground truth per meeting (length, speakers, words, summary sections), and which synced DBs hold a recording |
| `tenor ami wer --db data/ami_granola.db --product granola` | WER and content-word recall per meeting; numbers only, written to `data/ami/results/` |

Code: `tenor.ami` (`corpus` parses the zip in place, `wer` scores, `products` matches notes tagged `AMI <ID>`). Tests use a hand-made NXT zip, not the corpus.

**Parser finding:** NXT agent letters are per meeting. In IS1008 the same participant is agent B in sessions a and c and agent C in b and d, so cross-meeting questions must key on the participant code, not the letter.

## 11. Replay extension: 7 more meetings (2026-11-05, $0)

**Why:** 6 recorded meetings give 68 questions, too few for a tight interval. Kian approved 6–8 more files from the official mirror. Downloaded one at a time (25 s apart); checksums in [`MANIFEST.json`](../../data/ami/MANIFEST.json) (ES2006a, on disk since the stress track, was added there too); all verified as 16 kHz, 16-bit mono PCM.

**Download result: 6 of 7.** The mirror dropped two connections mid-file (ES2006c twice) and returned one HTTP 403 (TS3006b). Both files then completed and verified. After the 403 we stopped, so **ES2007c is not downloaded**: it stays in the set and in the questions (15), and `ami status` shows it as "not downloaded". Fetch it later, one file, after a pause. The 6 on disk play for **217.8 min (3 h 38 min)**.

**Rule, from the annotations zip (no LLM):** scenario meetings with an abstractive summary and summary links, 18–40 min, not already in the set; ranked by list questions (decision + action_what + problem, from `questions.py`), then all questions; **one meeting per team**, so no cross-meeting question skips a session; total ≤ 4.5 h. TS3009c was already on disk and counts as one of the seven.

| Meeting | Min (WAV) | List Qs | All Qs | Why this one |
|---|---|---|---|---|
| TS3009c | 43.0 | 7 | 12 | Already downloaded. **Over the 40-min ceiling**; kept because it was on disk |
| ES2015c | 35.6 | 13 | 20 | Most list questions of any eligible meeting |
| ES2008b | 37.2 | 11 | 18 | 2nd. (The dropout listed for ES2008 is in session c, not b) |
| ES2006c | 36.4 | 9 | 15 | 3rd. Same team as ES2006a (stress meeting), different session |
| TS3006b | 39.2 | 9 | 15 | Tied 3rd; a second TS team |
| ES2007c | 39.6 (listed size) | 8 | 15 | Next by all questions. **Not downloaded yet** |
| IS1000a | 26.4 | 8 | 14 | Tied with ES2010b on both counts; IS breaks the tie (site balance) and is shorter |
| **Total (all 7; 6 on disk = 217.8)** | **257.4 (4 h 17 min)** | **65** | **109** | |

Skipped: ES2015a (same team as ES2015c), ES2011b (mirror answered HTTP 403 to a HEAD request; not retried).

**Threats.** Five of seven are ES (Edinburgh) teams: the ranking favours ES annotators' fuller summaries. **None is in AMI's held-out dev/test partitions** (ASSUMED from the standard full-corpus partition; not re-read on datasets.shtml), so ASR contamination risk is higher than for IS1008 (§7): report WER on the extension separately from the original seven.

**Wiring.** `products.AMI_SET` is unchanged (the original 7): every scored command still runs on it, so no published number moves. `AMI_EXTENSION` holds the new 7 and `AMI_EXTENDED` both; `tenor ami status` lists all 14 with audio and recording state. Questions: `tenor ami extension-questions --seed 42` writes `data/ami_questions/replay_extension.jsonl` (all TEST; see its [README](../../data/ami_questions/README.md)). Playback order, longest first, downloaded meetings only: `data/ami/overnight_order.txt` (local, gitignored).
