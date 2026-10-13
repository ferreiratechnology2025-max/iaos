# UI provenance

- Original workspace: `D:\iaos-ui`
- Original HEAD: `7b6e1e88ddd351387e05cd6efc3e3197aca4b239`
- Imported into the IAOS repository as the product UI.

The UI is intentionally kept separate from the kernel modules. It talks to
the IAOS read-only gateway and to the existing OpenDots host; it does not
contain an agent loop or model credentials.
