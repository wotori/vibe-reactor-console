"""Ekza's shared asset lifecycle, with consumer-specific profiles kept as data.

This is an explanatory animation, never a network client or an admission oracle.
Space/Mirror accept avatars here; handheld weapons are demonstrated by OMOBA.
"""

from dataclasses import dataclass
from functools import lru_cache
import hashlib


@dataclass(frozen=True)
class Consumer:
    id: str
    title: str
    platform: str
    avatar_profile: str
    weapon_profile: str = ""

    @property
    def project_id(self):
        return "omoba" if self.id == "omoba" else "ekza-" + self.id


CONSUMERS = (
    Consumer("space", "EKZA SPACE", "universal", "vrm-humanoid-v1"),
    Consumer("mirror", "EKZA MIRROR", "ios", "arkit-body-v1"),
    Consumer("omoba", "OMOBA", "desktop", "humanoid-glb-v1", "handheld-glb-v1"),
)
CONSUMER_BY_ID = {item.id: item for item in CONSUMERS}
CONSUMER_IDS = ("all", *(item.id for item in CONSUMERS))
STAGES = (
    ("IDENTITY", "A stable ID names the asset, not a download URL."),
    ("REVISION", "Source revisions are immutable; publication is a separate gate."),
    ("RENDITION", "A platform/profile selects compatible model bytes."),
    ("APPROVAL", "Each consumer approves its exact rendition independently."),
    ("VERIFY", "The SDK checks byte count, SHA-256 and the consumer profile."),
    ("CONSUMER", "The consumer decides admission; peers verify their own downloads."),
)
STAGE_SECONDS = 3.5


@dataclass(frozen=True)
class ProtocolFrame:
    stage: int
    fraction: float
    kind: str
    identity: str
    revision: str
    digest: str
    consumer: Consumer
    profile: str
    explanation: str


@lru_cache(maxsize=64)
def rendition_digest(kind, revision):
    return hashlib.sha256(("demo rendition / " + kind + " / " + revision).encode()).hexdigest()


def protocol_frame(elapsed, consumer_id="all"):
    tick = max(0.0, elapsed) / STAGE_SECONDS
    cycle, stage = int(tick) // len(STAGES), int(tick) % len(STAGES)
    if consumer_id == "all":
        kind = "avatar" if cycle % 2 == 0 else "weapon"
        consumer = CONSUMERS[(cycle // 2) % len(CONSUMERS)] if kind == "avatar" else next(
            item for item in CONSUMERS if item.weapon_profile)
    else:
        consumer = CONSUMER_BY_ID[consumer_id]
        kind = "weapon" if consumer.weapon_profile and cycle % 2 else "avatar"
    profile = consumer.weapon_profile if kind == "weapon" else consumer.avatar_profile
    revision = "r{:04d}".format(cycle + 1)
    identity = "ekza:{}:7e92b410-18ad-4f06-a0bd-39377bdeb201".format(kind)
    return ProtocolFrame(stage, tick % 1, kind, identity, revision,
                         rendition_digest(kind, revision), consumer, profile, STAGES[stage][1])
