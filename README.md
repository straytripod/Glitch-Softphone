# Glitch Softphone

A desktop phone for Linux that works with your FusionPBX extension the way
a good desk phone does. It shows when colleagues are on the phone, does
transfers and call park, and handles call-center queues, paging and
intercom.

Runs on x86_64 and ARM64 (including Raspberry Pi 5).

## What it does

- **Calls:** dial, answer, hold, mute, keypad tones, up to four calls with
  call waiting
- **Busy lamps:** see at a glance who is free, ringing or on the phone.
  Click a lamp to call, or click a ringing lamp to answer for a colleague.
- **Transfers:** send a call straight on, or talk to the other person
  first
- **Call park:** park a call and pick it up from any phone
- **Paging and intercom:** answers pages and intercom calls by itself;
  start them with one click
- **Call-center queues:** join, leave and take a break
- **Status:** In Office, Away, At a Meeting… Each status can send your
  calls to your phone, voicemail or another number.
- **Contacts and history:** colleagues, the company phone book, your own
  contacts, and a list of recent calls
- **Voicemail lamp** with a count of new messages
- **HD audio** (Opus, G.722) and **encrypted calls** (TLS + SRTP)
- Tray icon and desktop notifications, light and dark themes, your own
  ringtone

## What you need

- A **FusionPBX** server and an extension on it. Ask your PBX
  administrator for the server name, your extension number and its
  password.
- A Linux desktop with a headset or speakers and a microphone.

Most features work on a standard FusionPBX. Some extras (colleague names,
intercom, queue breaks, encryption) need a few settings on the PBX. Your
administrator can find them in [docs/FUSIONPBX.md](docs/FUSIONPBX.md).

## Install

**[Download the latest release](https://github.com/straytripod/GlitchSoftphone/releases/latest).**
Pick **x86_64** for most PCs and laptops, or **aarch64** for ARM machines
such as the Raspberry Pi 5. To build it yourself instead, see
[docs/BUILD.md](docs/BUILD.md).

**AppImage:** nothing to install. Runs on Ubuntu 24.04+, Debian 13+,
Fedora 40+ and similar.

```bash
chmod +x Glitch_Softphone-*.AppImage
./Glitch_Softphone-*.AppImage
```

**Flatpak:**

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user Glitch_Softphone-*.flatpak
```

Then start **Glitch Softphone** from your app menu. To update, download
the next release and install it the same way.

## First start

1. The **Account** window opens. Enter:
   - **Server**: your PBX's name or address
   - **Extension** and **Password**
   - **Connection**: leave it on UDP unless your administrator says TLS
2. Click **Save**. The header shows **Registered** when you're connected.
3. Optional, under **Settings**:
   - **Audio and ringtone…**: choose your headset, and a separate speaker to
     ring on
   - **BLF extensions…**: add the colleagues you want lamps for
   - **Queue agent…**, **Paging and intercom…**, **Call routing…** if you
     use them

## Everyday use

- **Make a call:** type a number or a name, then press Enter or click
  **Call**.
- **Transfer:** during a call, click **Transfer**. Then choose
  **Transfer now**, or **Talk first** followed by **Complete Transfer**.
- **Park:** click **Park**. The call appears under *Parked calls*, where
  anyone can pick it up.
- **Colleagues:** click a lamp to call. Right-click it for intercom.
- **Refresh from PBX** (F5, in the Phone menu): loads lamps, names and
  company contacts your administrator just changed, without restarting.
- **Closing the window** keeps the phone running in the tray. To really
  quit, use **Phone → Quit** (Ctrl+Q).

## When something isn't right

**"Not registered" or "Registration failed":** check the server, extension
and password in **Settings → Account…**. After a few wrong passwords the
PBX may block your computer for a while. Ask your administrator to unblock
it.

**The other person can't hear you:** your microphone is probably muted in
the system sound settings or on the headset itself. Glitch Softphone never
changes system volume or mute. Also check that the right microphone is
chosen in **Settings → Audio and ringtone…**.

**Calls to you go straight to voicemail:** check your status and
**Settings → Call routing…**. A status set to *Voicemail* sends calls
there.

**For your administrator or a bug report:** the log file is
`~/.local/state/glitch-softphone/glitch-softphone.log` (Flatpak:
`~/.var/app/io.github.straytripod.GlitchSoftphone/.local/state/glitch-softphone/`).
Set `sip_trace = true` under `[logging]` in the config file to include
full SIP messages. Passwords, PINs and encryption keys are never written to
the log.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
