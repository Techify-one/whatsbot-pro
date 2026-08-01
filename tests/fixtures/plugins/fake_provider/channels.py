"""Real-loader entry point for the shared DB-free synthetic provider."""

from tests.fake_provider import FakeChannel


CHANNEL_PROVIDERS = [FakeChannel]
