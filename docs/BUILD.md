# Building Glitch Softphone

There are three ways to build Glitch Softphone. All of them work on x86_64
and ARM64 (aarch64) Linux.

| Option | Use it when | Result |
|---|---|---|
| [Flatpak](#flatpak) | You want a package to install on desktops | `dist/Glitch_Softphone-<version>-<arch>.flatpak` |
| [AppImage](#appimage) | You want a single file that runs without installing | `dist/Glitch_Softphone-<version>-<arch>.AppImage` |
| [From source](#run-from-source) | You want to change the code | Runs straight from the project folder |

Each package is built for the machine it's built on. To get an ARM64
package, build on an ARM64 machine, such as a Raspberry Pi 5.

Start by getting the code:

```bash
git clone https://github.com/straytripod/GlitchSoftphone.git
cd GlitchSoftphone
```

---

## Flatpak

**One-time setup:**

```bash
# Debian / Ubuntu
sudo apt install flatpak flatpak-builder
# Fedora
sudo dnf install flatpak-builder

flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.freedesktop.Platform//25.08 org.freedesktop.Sdk//25.08
```

The last command downloads the runtime the app runs on and the SDK it's
built with, about 1 GB.

**Build:**

```bash
scripts/build-flatpak.sh
```

The first build takes a while because it compiles PJSIP. Later builds reuse
that work and take seconds.

**Install and run:**

```bash
flatpak install --user dist/Glitch_Softphone-*.flatpak
flatpak run io.github.straytripod.GlitchSoftphone      # or start it from the app menu
```

To remove it: `flatpak uninstall --user io.github.straytripod.GlitchSoftphone`.

The Flatpak can use the network, sound, notifications and the tray icon. It
can't see your files: when you choose a ringtone or CA certificate, the
desktop's file picker hands over just that file.

---

## AppImage

**One-time setup:** install podman.

```bash
sudo apt install podman      # Debian / Ubuntu
sudo dnf install podman      # Fedora
```

**Build:**

```bash
scripts/build-appimage.sh
```

The build runs inside an Ubuntu 24.04 container, so the AppImage runs on
Ubuntu 24.04+, Debian 13+ and Fedora 40+. It includes Python, Qt, PJSIP and
OpenSSL.

**Run:**

```bash
chmod +x dist/Glitch_Softphone-*.AppImage
./dist/Glitch_Softphone-*.AppImage
```

If your system doesn't have FUSE, add `--appimage-extract-and-run`.

---

## Run from source

Use this to work on the code. The commands are for Debian/Ubuntu, with
Fedora equivalents noted.

### 1. Install the build tools

```bash
sudo apt install -y git python3-venv build-essential pkg-config swig \
    python3-dev libasound2-dev libssl-dev libopus-dev
```

On Fedora:

```bash
sudo dnf install -y git gcc gcc-c++ make pkgconf-pkg-config swig \
    python3-devel alsa-lib-devel openssl-devel opus-devel
```

| Package | Why |
|---|---|
| python3-venv | Keeps the app's Python packages separate from the system's |
| build-essential, pkg-config | Compiler and tools to build PJSIP |
| swig, python3-dev | Generate and compile PJSIP's Python bindings |
| libasound2-dev | Sound (PipeWire and PulseAudio work through it) |
| libssl-dev | TLS encryption |
| libopus-dev | Opus HD audio |

### 2. Create the Python environment

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Build PJSIP

PJSIP is the SIP and audio engine. Download version 2.17 and build it into
the environment:

```bash
mkdir -p ~/src && cd ~/src
curl -LO https://github.com/pjsip/pjproject/archive/refs/tags/2.17.tar.gz
sha256sum 2.17.tar.gz   # should be 065fe06c06788d97c35f563796d59f00ce52fe9558a52d7b490a042a966facce
tar xzf 2.17.tar.gz
cd -
scripts/build-pjsip.sh
```

This takes 5–20 minutes, depending on the machine. It's done when it prints
`pjsua2 OK, PJSIP 2.17`. If you ever recreate `.venv`, run
`scripts/build-pjsip.sh` again.

### 4. Run

```bash
.venv/bin/python -m app.main            # add --debug for detailed logs
```

On first start the app opens **Settings → Account…** so you can enter your
extension. You can also copy `config.example.toml` to
`~/.config/glitch-softphone/config.toml` and edit it by hand.

---

## Where the app keeps its files

| What | Flatpak | AppImage and source |
|---|---|---|
| Settings | `~/.var/app/io.github.straytripod.GlitchSoftphone/config/glitch-softphone/` | `~/.config/glitch-softphone/` |
| Call history, contacts, ringtones | `~/.var/app/io.github.straytripod.GlitchSoftphone/data/glitch-softphone/` | `~/.local/share/glitch-softphone/` |
| Log file | `~/.var/app/io.github.straytripod.GlitchSoftphone/.local/state/glitch-softphone/` | `~/.local/state/glitch-softphone/` |

To move an existing setup into the Flatpak, copy `config.toml` and
`settings.toml` into the Flatpak's settings folder. Then choose your CA
certificate again in Settings → Account…, because the Flatpak can't see
files in `~/.config`.
