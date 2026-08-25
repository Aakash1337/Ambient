# Ambient cybersecurity voice demo

This is a five-question, approximately five-minute demo. It shows behavioral
triage, ATT&CK mapping, evidence collection, competing hypotheses, and responsible
use of vulnerability sources.

## Before the audience arrives

1. Launch **Ambient** from the app menu.
2. Choose **Web Console**, then **Web Voice**.
3. Confirm the profile is **Cybersecurity analytics**.
4. Under **Interaction**, select **Agent**. Wait for Ambient's opening greeting to finish.
5. Select **Conversational** delivery and confirm the header says **VOICE ON**.
6. Wait for **Whisper ready on cuda** before speaking. CPU fallback works, but it is
   slower; for the smoothest demo, close competing GPU-heavy applications and
   relaunch Ambient if startup reports a CPU fallback.

Ask every question through the microphone. Ambient speaks only answers to the mic
channel by default. Wait until the complete spoken answer finishes, then leave about
one second before asking the next question. Do not speak over Ambient: mic and system
capture are intentionally muted during playback to prevent an audio feedback loop.

Generated wording may vary slightly, but the facts and conclusions below are the
grounded reference points.

## Question 1 — Initial assessment

Presenter transition: “I’ll begin with only three observed facts.”

Say exactly:

> Explain what this behavior most likely means: Microsoft Word spawned PowerShell,
> a scheduled task appeared, and the host contacted a newly registered domain.

Expected spoken answer:

> This is a high-suspicion execution chain with possible persistence. The leading
> hypothesis is a malicious document, template, or Office add-in launching a
> PowerShell stager, creating a scheduled task, and reaching external infrastructure.
> Legitimate Office automation remains possible, so validate the evidence before
> naming malware, an actor, or a vulnerability.

## Question 2 — ATT&CK mapping

Presenter transition: “Now I’ll ask it to separate evidence from inference.”

Say exactly:

> List the MITRE ATT&CK techniques for Word launching PowerShell and creating a
> scheduled task, and explain which mappings would be premature.

Expected spoken answer:

> The strongest mappings are PowerShell, T1059.001, and Scheduled Task, T1053.005.
> Add Malicious File, T1204.002, only if evidence shows the user opened a malicious
> file; Web Protocols, T1071.001, only for confirmed HTTP or HTTPS command-and-control
> traffic; and Ingress Tool Transfer, T1105, only if a download occurred. Ordinary
> DNS resolution alone does not establish command and control.

## Question 3 — Evidence to collect

Presenter transition: “Next I’ll move from classification to investigation.”

Say exactly:

> Describe the evidence an analyst should preserve and inspect next for this
> Word-to-PowerShell and scheduled-task sequence.

Expected spoken answer:

> Preserve the process tree and PowerShell command line, PowerShell 4103 and 4104
> events if enabled, AMSI-backed detections and EDR telemetry if available, the source
> document and Mark-of-the-Web, and the scheduled-task XML and event 4698 if audited.
> Then inspect proxy, TLS, DNS, download, and fleet-prevalence data. Isolate the host
> if the chain is untrusted while preserving evidence.

## Question 4 — Benign alternatives

Presenter transition: “Now I’ll test whether it considers competing explanations.”

Say exactly:

> Compare the malicious-document explanation for this Word-to-PowerShell chain with
> legitimate administrator automation, and explain how to distinguish them.

Expected spoken answer:

> Approved deployment scripting or an Office add-in could produce parts of this
> chain, although the full combination is suspicious. Check script signing and
> location, task creator and action, change records, user context, destination
> ownership, and peer-system prevalence. Obfuscation, hidden tasks, untrusted
> documents, or rare infrastructure favor the malicious explanation.

## Question 5 — ATT&CK versus vulnerability databases

Presenter transition: “Finally, I’ll test whether it avoids false precision.”

Say exactly:

> Explain when this investigation should use MITRE ATT&CK, NVD, and CISA KEV, and
> what each source contributes.

Expected spoken answer:

> ATT&CK organizes observed behavior, so it fits this chain immediately. NVD provides
> CVE details after the affected product and version are known. CISA KEV shows whether
> a matched CVE is known to be exploited in the wild; that helps prioritize
> remediation but does not prove this incident used it, and absence from KEV does not
> prove safety. Without product, build, or exploit evidence, do not name a CVE.

## Source references

- [MITRE ATT&CK data and tools](https://attack.mitre.org/resources/attack-data-and-tools/)
- [PowerShell — T1059.001](https://attack.mitre.org/techniques/T1059/001/)
- [Scheduled Task — T1053.005](https://attack.mitre.org/techniques/T1053/005/)
- [Malicious File — T1204.002](https://attack.mitre.org/techniques/T1204/002/)
- [Web Protocols — T1071.001](https://attack.mitre.org/techniques/T1071/001/)
- [Ingress Tool Transfer — T1105](https://attack.mitre.org/techniques/T1105/)
- [NVD vulnerability API](https://nvd.nist.gov/developers/vulnerabilities)
- [CISA Known Exploited Vulnerabilities catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog)
