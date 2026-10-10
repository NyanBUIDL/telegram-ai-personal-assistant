# Font provenance research — 2026-10-02

Status: unresolved for public redistribution. Original assets remain unchanged while implementation proceeds. This record establishes observed provenance; it does not grant a license.

## DarleySans in this checkout

`dashboard-prototype/public/fonts/DarleySans-Regular.otf`, SHA256 `8102ebd72fe1ef445f869ffa90c7a89d997e315fd424eba52fe4da7f3d555971`.

Font metadata identifies Daniel P. Lyons / LyonsType, copyright 2023, family Darley Sans, 410 glyphs. License and license URL name fields are blank. OS/2 fsType=0 is an embedding flag, not a replacement for a redistribution license.

Primary sources checked:

- [Current LyonsType fonts](https://lyonstype.neocities.org/fonts) and [bonus fonts](https://lyonstype.neocities.org/bonusfonts) state OFL for fonts located on those pages. DarleySans is not explicitly listed in the visible inventory.
- [Author's legacy FAQ](https://lyonstype4.wixsite.com/lyonstype-beta/faq) permits personal/commercial use of earlier fonts without a specified license. This general use statement does not identify this exact binary or provide its distribution notice.
- [Wordmark download terms](https://lyonstype4.wixsite.com/wordmark/download) and [FAQ](https://lyonstype4.wixsite.com/wordmark/faq) impose CC BY-NC-SA 4.0 and prohibit commercial use. Do not treat all author collections as OFL. The linked official archive was inspected without executing any content: 100 entries, archive SHA256 `3a3e7564e8fafa9c43f685cae37ced8f5091fd79cd82869514f09cea157978a9`; no Darley filename or family found. Therefore membership in Wordmark is not established.
- [Google Fonts submission #10814](https://github.com/google/fonts/issues/10814) points to [darley22/darleysans](https://github.com/darley22/darleysans). Public repository cloned at commit `227d9fdcc79808539b25164a9bbc75042b895897` (2026-08-15). OFL.txt and FONTLOG identify Duong Nguyen / Darley Sans Project Authors, while the TTF name records and UFO source retain Daniel P. Lyons / LyonsType and 2023 copyright. TTF SHA256 `79474090ef4dc6abb8542f6f7a55e7d114ddcb2661357edc2dfa10306644f894`; 410 shared glyphs, identical cmap and units; different binary and outline representation. This is strong provenance evidence of a related font, but the repository does not establish the upstream rights or explain the attribution discrepancy. An open submission is not Google Fonts approval.

Conclusion: do not copy the OFL.txt from a similarly named repository onto the original OTF and call the distribution gate verified. Obtain a font-specific author license/provenance notice covering the LyonsType original, or an explicitly approved replacement with documented rights. Preserve current art until this is resolved. No author has been contacted.

## PeterObscure

Original metadata reports LNTH-Peter Obscure, author Peter Wiegel, and SIL Open Font License. [Peter Wiegel's official site](https://www.peter-wiegel.de/index.html) describes commercial/software bundling permission for his own fonts and conditions on modifications. The LNTH variant still needs its exact upstream package and notice before artifact packaging. Presence of an OFL name record alone does not supply the required notices.

The [author's DaFont listing](https://www.dafont.com/peter-obscure.font) links the original archive. Downloaded archive SHA256 `83a8a448c5151e25085d88ebabc6a3d2ab62543003bb1da52cb4cfc3b9bf4713` contains OFL.txt (4,511 bytes), OFL-FAQ.txt and PeterObscure.ttf (51,964 bytes, 362 glyphs). Original TTF SHA256 `1c6b35c1ecd174876f18f28259755a3be99cf8000acdb31e5aaff20eb22e7c66`; its metadata matches the original 1995 Computer and Technologie copyright/Peter Wiegel authorship. OFL.txt supplies 2014 Peter Wiegel copyright and Reserved Font Name Peter Obscure. The project's LNTH binary is different (baseline hash in art-baseline.json). Preserve the upstream notices when packaging, and verify the modified variant's provenance and naming conditions. The original archive/notice is retained in ignored execution scratch for R01; no original was substituted into the product.

## Release handling

R01 must reconcile font identity, upstream attribution and included license notices. P01/P02 may prepare internal build candidates; G5/public distribution remains pending while font redistribution is unresolved. No font binary, filename, styling or copyright metadata was changed during this investigation.
