# How I Automated Meeting Follow-Up with Granola + OpenClaw

Every meeting generates action items. Most of them die in the gap between "we should do that" and actually doing it.

I fixed this by wiring Granola (AI meeting notes) directly into my OpenClaw agent. Now every meeting I take gets automatically transcribed, summarized, routed to the right project, and turned into tracked action items. No manual step. No copy-paste. No "let me review my notes later."

Here's the full pipeline.

## The Problem

I run multiple projects with different collaborators. A typical week has 6-8 meetings across 3-4 projects. Before this setup:

- Meeting notes sat in Granola unread
- Action items lived in my head (or didn't)
- Project context never got updated
- I'd show up to the next meeting having forgotten half of what we decided

The real cost isn't the meetings. It's the knowledge that evaporates between them.

## Architecture

```
Granola (Mac) → Cache Watcher Daemon → Webhook → VPS Receiver → Cron Processor → Telegram Summary
```

Five components:

1. **Granola** records and transcribes meetings on Mac
2. **Cache watcher daemon** on Mac detects new meetings in Granola's local cache
3. **Webhook receiver** on VPS accepts the meeting payload
4. **Cron processor** classifies and summarizes every 3 minutes
5. **Telegram delivery** sends a structured summary with action items

The trick: Granola doesn't have native webhook support (it's on their roadmap). So we watch Granola's local cache file for changes and fire our own webhooks. Credit to [owengretzinger/granola-webhook](https://github.com/owengretzinger/granola-webhook) for the original idea.

Total setup time: ~30 minutes. Cost: $0. Granola's free tier only keeps 14 days of meeting history, but it doesn't matter — our cache watcher captures everything in real-time and stores it permanently on the VPS. You get unlimited meeting history without paying for a Granola subscription.

## Step 1: The Cache Watcher (Mac Side)

Granola stores meeting data in a local cache file at:
```
~/Library/Application Support/Granola/cache-v4.json
```

This file gets updated whenever a meeting ends. A small daemon watches it for changes and sends the new meeting data to your VPS.

The daemon runs as a macOS launchd agent (`com.granola.webhook`), which means it starts on login and auto-restarts if it crashes.

Key details about the cache:
- Contains full meeting documents (title, attendees, notes)
- Also contains transcripts with timestamps and speaker identification
- Only keeps ~5 recent transcripts locally (Granola purges older ones from cache to save disk)
- This means the daemon needs to catch transcripts in real-time while they're still in cache

The daemon compares document IDs on each cache change, and when it finds a new one, it sends the full payload (document + transcript if available) to the VPS webhook endpoint.

### Launchd Setup

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.granola.webhook</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/node</string>
        <string>/path/to/granola-webhook-daemon.js</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/granola-webhook.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/granola-webhook.log</string>
</dict>
</plist>
```

Save to `~/Library/LaunchAgents/com.granola.webhook.plist` and load:
```bash
launchctl load ~/Library/LaunchAgents/com.granola.webhook.plist
```

Check status: `launchctl list com.granola.webhook`

## Step 2: Webhook Receiver (VPS Side)

A lightweight Node.js server that accepts the meeting payload from Mac.

```javascript
const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = 8790;
const TOKEN = 'your-secret-token-here';
const DATA_DIR = path.join(process.env.HOME, 'clawd/data/granola-webhooks');

fs.mkdirSync(DATA_DIR, { recursive: true });

const server = http.createServer((req, res) => {
  if (req.method !== 'POST' || req.url !== '/webhook') {
    res.writeHead(404);
    return res.end('Not found');
  }

  const auth = req.headers.authorization || '';
  if (auth !== `Bearer ${TOKEN}`) {
    res.writeHead(401);
    return res.end('Unauthorized');
  }

  let body = '';
  req.on('data', chunk => body += chunk);
  req.on('end', () => {
    try {
      const data = JSON.parse(body);
      const title = data?.data?.title || 'untitled';
      const slug = title.toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .slice(0, 50);
      const ts = new Date().toISOString()
        .replace(/[:.]/g, '-')
        .slice(0, 19);
      const filename = `${ts}-${slug}.json`;

      // Save full meeting data
      fs.writeFileSync(
        path.join(DATA_DIR, filename),
        JSON.stringify(data, null, 2)
      );

      // Mark as pending for processor
      const pendingDir = path.join(DATA_DIR, '.pending');
      fs.mkdirSync(pendingDir, { recursive: true });
      fs.writeFileSync(path.join(pendingDir, filename), '');

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end('{"status":"ok"}');
    } catch (e) {
      res.writeHead(400);
      res.end('Bad request');
    }
  });
});

server.listen(PORT, () => {
  console.log(`Granola webhook receiver on port ${PORT}`);
});
```

I bind this to my Tailscale IP so only devices on my private network can reach it. No public internet exposure.

Run as a systemd service:
```bash
# /etc/systemd/system/granola-webhook.service
[Unit]
Description=Granola Webhook Receiver
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/node /root/clawd/scripts/granola-webhook-server.js
Restart=always

[Install]
WantedBy=multi-user.target
```

## Step 3: The Processor (OpenClaw Cron)

An OpenClaw cron job checks for unprocessed meetings every 3 minutes.

```bash
openclaw cron add \
  --name "granola-processor" \
  --every "3m" \
  --model "sonnet" \
  --session isolated \
  --message 'Check ~/clawd/data/granola-webhooks/.pending/ for unprocessed meetings. 
  If empty, reply NO_REPLY. 
  If files exist, for each one: 
  1) Read the matching JSON 
  2) Extract title, attendees, transcript, notes 
  3) Classify project based on attendees and content
  4) Summarize key discussion points from transcript 
  5) Send a Telegram summary with: title, attendees, bullet summary, action items 
  6) Delete the .pending marker file'
```

When idle: instant NO_REPLY, minimal token cost. When a meeting lands: reads the full transcript and produces a structured summary.

### Project Classification

The processor routes meetings automatically based on:
- **Attendee names** (e.g., "your co-founder" = ads platform, "your collaborator" = curriculum project)
- **Content keywords** (e.g., "curriculum" = education project, "dashboard" = ads platform)
- **Meeting title** patterns

No manual tagging. The summary lands in the right context.

## Step 4: What You Get

Within 3 minutes of ending a meeting, a Telegram message arrives:

```
Meeting: Weekly Team Planning
Attendees: You, Collaborator A, Collaborator B
Date: Recent date

Key Points:
- Module structure being reorganized into weekly format
- Newsletter subscriber count growing steadily
- First paid cohort launching soon

Action Items:
- You: Promote on LinkedIn once the community link is live
- Collaborator A: Convert L1-L3 handouts to Gamma slides
- Collaborator B: Draft marketing email for 40K list

Project: community-project
```

## Why Not Zapier?

My first version used Zapier to send Granola notes to Google Drive, then an OpenClaw cron to check Drive for new files. It worked but:

- Zapier's free tier ran out fast — this would cost $20+/mo to maintain
- Extra storage layer (Google Drive)
- Slower (Zapier polling + Drive API)
- Missed transcripts (Zapier only got the notes, not the full transcript)

The cache watcher approach is direct: Mac → VPS, no middleman. Zero ongoing cost. And you get the full transcript, not just the AI-generated notes.

## Taking It Further

### Task creation
Action items can automatically become tracked tasks in your task database. Each action item gets the right project tag and priority.

### Meeting prep
Before your next meeting with the same people, pull the last meeting summary and surface unfinished action items. "Last time you agreed to X. Did that happen?"

### Pattern detection
After a few weeks of data, notice patterns. "You've discussed pricing in 4 of the last 5 meetings with your co-founder but haven't finalized it."

### Backfilling old meetings
The cache only keeps ~5 recent transcripts. To get older transcripts, you'd need Granola's Enterprise API (not yet available) or catch them in real-time going forward. The key lesson: set this up before you need it.

## The Full Stack

| Component | Tool | Cost |
|-----------|------|------|
| Meeting recording + transcription | Granola (Mac) | Free (cache watcher bypasses 14-day limit) |
| Cache watcher daemon | Node.js + launchd (Mac) | $0 |
| Webhook receiver | Node.js on VPS | $0 |
| AI processing | OpenClaw + Claude | Part of existing usage |
| Delivery | Telegram | Free |

## Why This Works

Zero manual steps. You don't need to:
- Open Granola after the meeting
- Copy notes somewhere
- Create action items manually
- Update project files
- Brief yourself before the next meeting

The gap between "meeting ended" and "everything is tracked" is 3 minutes.

Most productivity tools fail because they add a step. This removes steps. Your meetings become self-documenting.

---

*I teach people how to build AI automation workflows like this in my paid community. If you want to set up your own agent systems, check it out.*
