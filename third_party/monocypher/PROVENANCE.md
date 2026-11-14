Monocypher 4.0.3, deliberately refreshed from 4.0.2 using the official release:
https://monocypher.org/download/monocypher-4.0.3.tar.gz

Archive hashes (SHA-512 and BLAKE2b verified against the published checksum files beside that archive):
- SHA-256: 8cc9bc341a66249016db9bd70e9142d8d0aef9945973744b1ac05dbc55d8ee66
- SHA-512: 40904ada5c7ee4f7741733e38b69a30a4b0561cbffba5ffe7c2dce16136d540251ec0d9056ff606510d3b5b708fb8a40db7e0870d4a0b2dc17ba2bfb880f8965
- BLAKE2b: 5c586019e8e78ce6fb70f73f4ded10c852335138394b22fb704d0b4379edfc1adaea342f0e4c4b8c52fcf053df401d1fe47812ee5cb5062813de19fc51f8ae93

Unmodified release members: src/monocypher.c and src/monocypher.h, kept here without the src/ prefix, and LICENCE.md.
The licence is unchanged from 4.0.2: dual-licensed BSD-2-Clause or CC0-1.0; used here under the GPL-compatible BSD licence.
The GitHub mirror's 4.0.3 tag is ab2b16dd619ad5f6979a4fbe69cfa324a6fcc35f. Its source differs only in the unstamped
__git__ version comment; the release licence omits the tag's note about tests/externals, which is not in this release.

4.0.3 fixes a compiler-dependent timing leak in EdDSA/Ed25519 signatures, also hardening the related field-element
swap, and clears BLAKE2b's temporary key block. The public header declarations are unchanged. See the upstream
CHANGELOG.md and https://monocypher.org/download/ for the release notes and the warning covering 4.0.2.

Compiled into the runtime by nx2/build.py. host/coop_proto.c uses keyed BLAKE2b (RFC 7693) for room keys and datagram
tags, XChaCha20-Poly1305 for sealed messages, EdDSA over Curve25519 with BLAKE2b for individual signatures, and wiping
of temporary plaintext (docs/coop.md). The layered co-op work in pull request 355 additionally uses this library
directly in host/coop_profile.c for copied-save and checkpoint hashes, and host/coop_save.c for transfer hashes and
state wiping. Those two consumers arrive with that follow-on, not with this base. Neither layer calls X25519.

tree-sha256: 14c21311b939074fc75761cbdd3d87919e15cda2edd240981735d4c804b261b1
