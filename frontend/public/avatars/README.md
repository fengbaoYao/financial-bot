# Avatar SVG state snapshots

`public/avatars/svg/` contains the six state snapshots extracted from the
official xAI design article:

- https://x.ai/news/designing-grok-bot
- Section: "Avatar motion system"

The official page uses a React component with an inline SVG and a custom
`requestAnimationFrame` engine. It does not expose GIF/MP4 files. These SVG
snapshots are used as the base geometry for the local `GrokAvatar` component.
The component parses the official body/eye paths, morphs them with `flubber`
during state transitions, and runs a local RAF loop for eye motion and blinking.

The source page/artwork belongs to xAI and is used for the internal Financial
Bot prototype. Replace with fully licensed assets before commercial
distribution.

State mapping:

| Bot state | Snapshot |
|---|---|
| idle | idle.svg |
| queued | thinking.svg |
| running | working.svg |
| waiting | waiting.svg |
| completed | done.svg |
| failed / partial | blocked.svg |
| cancelled / unknown | idle.svg |
