"""Curated Ekza/OMOBA source tour, based on the workspace architecture.

Only these source files are read. Paths are relative to the Ekza umbrella folder.
Snapshots are exact excerpts captured on 2026-10-30, with original line numbers.
"""

from .domain import Mode, Snippet


EKZA_MODE = Mode(
    id="ekza", title="EKZA SPACE", subtitle="PROTOCOL / AVATARS / TYPED ASSETS",
    description="Ekza's shared asset lifecycle, consumed by Space, Mirror, OMOBA and other compatible apps.",
    palette="neon", geometry="avatar", workspace="ekza / identity -> revision -> rendition", hardware=False,
    snippets=(
        Snippet("ekza-bevy-sdk/src/assets.rs", "Rust", "Canonical asset identity + exact rendition",
                "ekza-assets.rs.txt", "ekza-bevy-sdk/src/assets.rs", "pub fn asset_slug(", 113, 48),
        Snippet("ekza-bevy-sdk/src/store.rs", "Rust", "Verify bytes, validate profile, install atomically",
                "ekza-store.rs.txt", "ekza-bevy-sdk/src/store.rs", "pub fn install(", 266, 54),
        Snippet("omoba-bevy/server/src/passport_admission.rs", "Rust", "OMOBA server owns admission; clients cannot grant it",
                "omoba-admission.rs.txt", "omoba-bevy/server/src/passport_admission.rs",
                "let Some(slug) = avatar.as_deref()", 270, 50),
        Snippet("core/src/features/ekza/useVerifiedAvatar.ts", "TypeScript", "Space peers verify server-approved avatars",
                "ekza-space-avatar.ts.txt", "core/src/features/ekza/useVerifiedAvatar.ts",
                "export function useVerifiedAvatar(", 6, 40),
        Snippet("ekza-stellar-sdk/src/catalog/index.ts", "TypeScript", "Web SDK: pinned references + verified VRM download",
                "ekza-web-sdk.ts.txt", "ekza-stellar-sdk/src/catalog/index.ts",
                "export function avatarReference(", 154, 33),
        Snippet("omoba-bevy/client/src/ekza_account.rs", "Rust", "Project-scoped account session + stale response guard",
                "omoba-account.rs.txt", "omoba-bevy/client/src/ekza_account.rs",
                "fn finish_refresh(", 67, 45),
        Snippet("core/src/realtime/protocol.ts", "TypeScript", "Space realtime: presence, rooms, movement and avatars",
                "ekza-realtime.ts.txt", "core/src/realtime/protocol.ts",
                "export const REALTIME_EVENTS", 3, 56),
        Snippet("solana-ekza-space/programs/ekza-space/src/instructions/mint_space.rs", "Rust",
                "Optional Solana layer: numbered Space NFT mint",
                "ekza-mint-space.rs.txt", "solana-ekza-space/programs/ekza-space/src/instructions/mint_space.rs",
                "pub fn mint_space(", 128, 48),
    ),
    phases=("READ", "ID", "HASH", "PROFILE", "EQUIP", "READY"),
    phase_messages=(
        "", "[sim] identity + rendition hash pinned to the asset slug",
        "[sim] exact size / SHA-256 / GLB envelope verified",
        "[sim] project / platform / profile select a compatible rendition",
        "[sim] each consumer decides admission and resolves its own files",
        "[sim] selected release retained | typed asset contract ready",
    ),
    events=(
        ("ASSET", "ekza:avatar:<uuid> + rendition.sha256 -> immutable slug"),
        ("SDK", "exact size + SHA-256 checked before atomic install"),
        ("CONSUMER", "Space / Mirror / OMOBA use independent profile selectors"),
        ("AVATAR", "stable avatar identity is independent of model format"),
        ("RULE", "new publication preserves each game's selected release"),
        ("PROFILE", "platform + profile + project approval pin the exact bytes"),
        ("ACCOUNT", "device link reads a project library; it grants no admission"),
        ("WEAPON", "appearance + semantic grip | combat stats remain game-owned"),
        ("SOLANA", "optional ownership layer | free Studio assets need no wallet"),
        ("PASS", "source tour / protocol visualization | no network requests"),
    ),
)
