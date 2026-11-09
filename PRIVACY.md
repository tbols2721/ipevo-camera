# Privacy and public contributions

Keep personal names, email addresses, usernames, hostnames, local home paths,
credentials, and device identifiers out of commits and issue reports. Review
text, screenshots, and generated artifacts before sharing them.

Inspect the staged files and Git identity before a contribution:

```sh
git diff --cached --check
git diff --cached
git status --short
git var GIT_AUTHOR_IDENT
git var GIT_COMMITTER_IDENT
```

Use an appropriate non-identifying Git name and email for public work. A GitHub
noreply address may still associate a commit with a profile. Repository owner
names and public GitHub activity remain visible.

Ignore rules reduce accidental additions. They do not redact terminal output,
issue comments, release assets, or files deliberately staged with `git add -f`.
Removing a file in a later commit does not erase previous Git history.

## Photos and local tool results

A photo can contain private documents, handwriting, faces, screens, or background
objects. Prepare the scene before requesting capture. The server returns the
image and an absolute local output path to its client. A connected assistant
may process that content using its normal network and data-handling behavior.
The absence of network calls inside this server does not make that processing
offline.

New image files are created with owner-only access. This does not redact the
scene, remove image metadata, or delete copies already sent to a client.
Saved photos remain under `~/Pictures/IPEVO` until removed. Existing directory
permissions are not changed. Never commit photos, client transcripts, or real
camera-test artifacts to this repository.

The test suite uses synthetic images and fake controls. Prefer those fixtures
when reporting a defect. Camera names, paths, terminal prompts, and screenshots
also need inspection before sharing.
