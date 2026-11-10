# Setting up FusionPBX for Glitch Softphone

This guide is for whoever runs your FusionPBX server. It explains what to
set up on the PBX so each Glitch Softphone feature works.

Most features work on a standard FusionPBX with no changes. Start with
[Step 1](#step-1-create-the-extension), then turn on only the extras you
want.

## At a glance

| Feature | PBX work needed |
|---|---|
| Calls, hold, transfer, call waiting | None |
| Busy lamps (BLF) and call pickup | None |
| Call park | None |
| Status (presence) | None |
| Call routing by status (voicemail / forward) | None |
| HD audio (G.722) | None |
| Voicemail lamp | None. Optional: one line, so the count shows at start-up |
| Colleague names, contacts, lamps handed out by the PBX | Turn on provisioning and add a device for each computer |
| Paging | Create page groups (as for desk phones) |
| Two-way intercom | Change one dialplan entry |
| Call-center queue agent | Create the agent. Optional: add a "break" code |
| HD audio with Opus | Turn on one module |
| Encrypted calls (TLS + SRTP) | Certificate, two variables, and one setting per extension |

Menu paths below are from the FusionPBX web interface. Commands marked
*on the PBX* are typed in a shell on the PBX server.

---

## Step 1: Create the extension

1. **Accounts → Extensions → +**. Set the extension number and a strong
   password. That's all the phone needs. You don't need a FusionPBX
   *user*: that's only a login for the web interface.
2. Give the user these details for the app's **Settings → Account…**
   window:
   - **Server**: the FusionPBX **domain** name, the same one desk phones
     use. If your domain is an IP address, enter that address.
   - **Extension** and **password**.
   - **Connection**: UDP or TCP on port 5060 work out of the box. For TLS,
     see [Encrypted calls](#encrypted-calls-tls--srtp).

**Check:** in **Status → Registrations** the extension shows up with
*Glitch Softphone* as its user agent.

**If registration times out:** after a few wrong passwords, FusionPBX's
fail2ban blocks the computer's IP address, and from then on registration
just times out. On the PBX:

    fail2ban-client status                       # list the jails
    fail2ban-client set <jail> unbanip <address>

---

## Calls, transfers and call waiting

Nothing to set up. Blind transfer, attended transfer ("Talk first") and
call park work with a standard FusionPBX.

- **A transfer to voicemail or the echo test fails.** That's expected:
  you can only transfer a call that has a person on the other end.
- **A second caller gets busy or voicemail even though call waiting is on
  in the app.** Check the extension's **Limit Max** (Accounts → Extensions →
  Advanced) and any *forward on busy*.

---

## Busy lamps (BLF) and call pickup

Nothing to set up. The user adds colleagues in **Settings → BLF
extensions…**. Each lamp shows idle, ringing or busy.

Clicking a **ringing** lamp answers that call, using FusionPBX's directed
pickup code `**` + extension. If pickup is refused:

- check that the **intercept-ext** entry is enabled in **Dialplan →
  Dialplan Manager**;
- check whether pickup is limited to call groups.

If your PBX uses a different pickup code, set `pickup_prefix` in the app's
config file.

### Handing out lamps from the PBX

You can give users their lamps from FusionPBX, the same way desk phones get
BLF buttons. This needs [provisioning](#names-contacts-and-pbx-lamps-provisioning)
set up first.

1. **Accounts → Devices →** (the computer's device) **→ Keys**. Add one row
   per lamp:
   - **Category** `line`
   - **Type** `BLF`
   - **Value**: the extension to watch
   - **Label** (optional): replaces the name from the directory
2. For a whole team, put the keys on a **Device Profile** (Accounts →
   Device Profiles) and choose that profile on each device.

The app loads these lamps when it starts and every hour after that. To
pick up a change straight away, the user presses **F5** (**Phone → Refresh
from PBX**); names and company contacts are reloaded too. PBX lamps
come first in the list, followed by any lamps the user added. Users can't
remove PBX lamps in the app; delete the key in FusionPBX instead.

---

## Names, contacts and PBX lamps (provisioning)

With this set up, the app shows:

- colleagues' names next to their lamps;
- a company phone book on the Contacts tab;
- lamps handed out by the PBX ([above](#handing-out-lamps-from-the-pbx)).

It downloads these the same way a Yealink desk phone does, using
FusionPBX's built-in provisioning. No add-ons are needed.

### Once per PBX

**Advanced → Default Settings**, search for `provision`. For each row
below, set the value and **turn on its Enabled switch**. Then click
**Reload**.

| Setting | Value | Why |
|---|---|---|
| `enabled` | `true` | Turns provisioning on |
| `contact_extensions` | `true` | Puts extensions in the phone directory |
| `cidr` | your office network, e.g. `192.0.2.0/24` | Only computers on this network can download settings. Add one row per network. |

> **Important:** provisioning gives every device's settings, including desk
> phones' SIP passwords, to anyone who asks with that device's MAC address.
> Always set `cidr` so this only works from your own networks. Users
> working remotely need their network (or VPN range) added as another
> `cidr` row.

### Once per computer

1. In the app, open **Settings → BLF extensions…** and note **This
   computer's MAC address**.
2. In FusionPBX: **Accounts → Devices → +**
   - **MAC Address**: the address from step 1
   - **Template**: `yealink/t46s`
   - **Lines → 1**: the user's extension, with the PBX domain as server
     address

The first time the app connects to the PBX's web server, it may ask
whether to trust its certificate. This happens when the certificate is
self-signed, as on a fresh FusionPBX. To check that the fingerprint the
app shows is really your PBX's, run this on the PBX:

    openssl x509 -in /etc/ssl/certs/nginx.crt -noout -fingerprint -sha256

From then on the app accepts only that certificate. With a properly signed
certificate (such as Let's Encrypt) the app doesn't ask.

### Where names and contacts come from

- **Names**: each extension's **Directory First/Last Name**, or else its
  **Effective Caller ID Name**. Extensions that are disabled or have
  **Directory Visible** turned off are left out.
- **Company contacts**: **Apps → Contacts**. A contact is included when it
  has a phone number with **Voice** ticked. If FusionPBX's contact
  permissions are on, each user only sees contacts shared with them or their
  groups. Users can't edit these in the app.
- **Personal contacts**: stored only on the user's computer.

When an outside caller's number matches a contact, the app shows the
contact's name.

The app keeps only the BLF keys from the device's settings file. It
never saves or logs the SIP password in that file.

### Troubleshooting

The status under the list in **Settings → BLF extensions…** says whether
names loaded. After changing something on the PBX, click **Refresh from
PBX** there to try again. FusionPBX answers every refusal with "404 not found", so look
in the PBX's system log for the reason:

    sudo grep -h "provision attempt" /var/log/syslog /var/log/auth.log | tail -5

| Log says | Fix |
|---|---|
| *provisioning is … not enabled* | Set the `enabled` row, turn on its switch, click Reload |
| *failed CIDR check* | Add the computer's network as a `cidr` row |
| nothing at all | No device with that MAC address in that domain |
| (names load, but the list is empty) | Turn on `contact_extensions`; check **Directory Visible** on the extensions |

---

## Call park

Nothing to set up. The app uses FusionPBX's built-in park slots, `*5901`
to `*5999`.

- **Park** puts the call in the first free slot. Anyone can pick it up by
  dialing that slot, or by clicking it under *Parked calls* in the app.
- A parked call that nobody picks up rings back after 15 minutes.
- The app shows slots 5901–5904 by default. Change this with `first_slot` and
  `slots` in the `[park]` section of the config file.

---

## Status and call routing

**Status** (In Office, Away, At a Meeting…) needs no setup. Other users of
Glitch Softphone see it under the user's lamp.

**Call routing by status** (Settings → Call routing…) lets each status send
calls to the user's phone, to voicemail, or to another number. The app does
this by quietly dialing FusionPBX's standard codes, so the setting lives on
the PBX. It keeps working while the app is closed, and applies to every
phone on that extension.

| Route | Code it dials |
|---|---|
| Voicemail | `*78` (Do Not Disturb on) |
| Back to ringing | `*79` or `*73` |
| Forward to a number | `*72` + number |

Things to know:

- **`*78` also logs the user out of call-center queues.** This is how
  FusionPBX behaves, not the app.
- Forwarding to an outside number is an outside call, so the extension's
  toll allow and outbound routes must permit it.
- If someone changes Do Not Disturb or forwarding elsewhere (a desk phone
  or the web page), the app won't know. Choosing the status again in the
  app sets it right.

---

## Voicemail lamp

Nothing to set up. The **Voicemail** button turns red and shows the number
of new messages. Clicking it dials `*97` to listen.

**Optional: show the count when the app starts.** Out of the box, the count
only appears after the next new or deleted message. To have FusionPBX send
the count right away, on the PBX uncomment this line in
`/etc/freeswitch/autoload_configs/lua.conf.xml`:

    <param name="startup-script" value="app/voicemail/resources/scripts/mwi_subscribe.lua"/>

Then run:

    fs_cli -x "reloadxml"
    fs_cli -x "lua app/voicemail/resources/scripts/mwi_subscribe.lua"

Desk phones benefit from this too.

---

## Paging and intercom

- **Paging** is a one-way announcement to a group of phones.
- **Intercom** is a two-way call to one phone that answers by itself.

The app answers both by itself when it isn't already on a call. Pages play
on the ringer speaker with the microphone off. Intercom calls use the
headset with the microphone on. Users can turn auto-answer off in
Settings.

### Paging

Create page groups under **Apps → Paging** as you would for desk phones.
Users add them to the **Page** button in **Settings → Paging and
intercom…** (group name and number).

Tips:

- After creating or changing a page group, use **Status → SIP Status →
  Flush Cache** if dialing it says "temporarily unavailable".
- Leave **Announcement** at *None* for live pages. With a recording
  selected, the caller is disconnected at once.
- The person paging isn't paged themselves. To reach every member, page
  from a phone that isn't in the group.
- If pages sound like static or cut out quiet words, lower `energy-level`
  (to about 100) and turn off `comfort-noise` in **Advanced → Conference
  Profiles → page**.

### Two-way intercom

Out of the box, FusionPBX's `*8` + extension is a one-way page that asks
for a PIN. To make it a two-way intercom the app recognizes:

1. **Dialplan → Dialplan Manager → page-extension**.
2. In the actions:
   - delete the row `set pin_number=...` (no PIN prompt);
   - change `mute=true` to `set mute=false` (two-way);
   - add a `set` action with this data:

         auto_answer=sip_h_Call-Info=<sip:${domain_name}>;answer-after=0;intercom

3. Make sure every `set` row has a **lower Order number** than the
   `lua page.lua` row. New rows are added at the bottom, which is too late.
4. Save, then **Status → SIP Status → Flush Cache**.

Users start an intercom call with the **Intercom** button, or by
right-clicking a lamp. If your PBX uses another code than `*8`, set it in
Settings → Paging and intercom….

---

## Call-center queue agent

Users join and leave queues, and take breaks, from the app. It uses
FusionPBX's call-center feature codes and keys in the agent PIN for the
user.

### Set up the agent

1. **Apps → Call Center → Agents → +**:
   - **Agent Name** and **Agent ID**: the user's extension number. Use the
     same value for both.
   - **Password**: the agent PIN
   - **Contact**: the user's extension
2. **Apps → Call Center → Queues** → the queue → add the agent under
   **Agents**, **Save**, then click **Reload**.
3. In the app: **Settings → Queue agent…**, turn it on and enter the agent
   ID and PIN.

**If the agent never rings:** check that the agent is listed in the queue
(step 2), then run `fs_cli -x "callcenter_config tier list"` on the PBX.

### Optional: a "break" code

FusionPBX has `*22` to log in and out, but no code for *On Break*. To add
`*24`:

1. **Dialplan → Dialplan Manager**, open **agent_status** (the one for
   `*22`) and copy it. Name the copy **agent_break**.
2. Change its condition to `destination_number` `^\*24$`.
3. Actions, in this order:
   - `set` `agent_id=${sip_from_user}`
   - `set` `agent_action=break`
   - `lua` `app.lua agent_status`
4. Save, then Flush Cache.

### Optional: show the agent status when the app starts

Out of the box the app shows *Unknown* until the agent status next changes.
To have FusionPBX send it right away, on the PBX uncomment this line in
`/etc/freeswitch/autoload_configs/lua.conf.xml`:

    <param name="startup-script" value="blf_subscribe.lua agent"/>

Start it once now with `fs_cli -x "luarun blf_subscribe.lua agent"`. This
only works if the agent name equals the agent ID.

---

## HD audio

The app prefers HD codecs: Opus first, then G.722, then standard phone
quality (PCMU/PCMA).

- **G.722** works on a standard FusionPBX.
- **Opus** (optional, better quality on poor networks):
  1. **Advanced → Modules → Opus**: Enabled = true, then **Start**.
  2. **Advanced → Variables → Codecs**: put `OPUS` first in
     `global_codec_prefs`, e.g. `OPUS,G722,PCMU,PCMA`.
  3. **Status → SIP Status**: **Reload XML**, then restart the *internal*
     profile. Phones re-register within a minute.

Calls to outside numbers usually stay standard quality, because most SIP
trunks only support PCMU/PCMA.

To see which codec a call is using, run `fs_cli -x "show channels"` on the
PBX during the call and look at the `read_codec` column.

---

## Encrypted calls (TLS + SRTP)

TLS encrypts the call setup and SRTP encrypts the audio. With TLS on, the
app requires SRTP by default: a call that can't be encrypted fails instead
of going through unencrypted. A padlock next to a call means it's
encrypted.

### 1. A certificate for the PBX

The app checks the PBX's certificate. Either:

- **Recommended:** a certificate from a public CA (e.g. Let's Encrypt) for
  the PBX's DNS name. The app trusts it with no extra setup.
- **Your own CA**, if the PBX has no DNS name. On the PBX
  (`/etc/freeswitch/tls` must be empty first):

      sudo gentls_cert setup -cn "My PBX CA" -org "My Company"
      sudo gentls_cert create_server -cn <pbx address> -alt IP:<pbx address> -org "My Company"
      sudo chown -R www-data:www-data /etc/freeswitch/tls

  Use `-alt DNS:<name>` instead of `IP:` if phones connect by host name.
  Then give users `/etc/freeswitch/tls/cafile.pem`. It's a public file,
  not a secret. They choose it under **Settings → Account… → Advanced → CA
  certificate**.

### 2. Turn on TLS

1. **Advanced → Variables**: `internal_ssl_enable` = `true` and
   `sip_tls_version` = `tlsv1.2`. Save.
2. On the PBX:

       fs_cli -x reloadxml
       fs_cli -x "sofia profile internal restart"

3. Check: `fs_cli -x "sofia status profile internal" | grep -i tls` shows
   port 5061.

In the app, choose **TLS (encrypted)** under Settings → Account…. Leave
Port at *Default* and the app uses 5061.

### 3. Encrypt calls *to* the extension

Calls the user makes are encrypted with no extra setup. Calls *to* the
extension need one change per extension, or the app refuses them and they
go to voicemail.

**Accounts → Extensions →** (extension) **→ Advanced → Dial String**:

    {rtp_secure_media_outbound=true,sip_invite_domain=${domain_name},leg_timeout=${call_timeout},presence_id=${dialed_user}@${dialed_domain}}${sofia_contact(*/${dialed_user}@${dialed_domain})}

This is FusionPBX's default dial string with `rtp_secure_media_outbound=true`
added at the start. Only set it on extensions that use TLS. A phone on
plain UDP would refuse these calls.

Encryption covers the path between the app and the PBX. A call to a phone
that doesn't use encryption is unencrypted on that phone's side.

**If registration fails with a certificate error:** the name in the
certificate doesn't match the **Server** the user entered, or the CA file
is the wrong one.
