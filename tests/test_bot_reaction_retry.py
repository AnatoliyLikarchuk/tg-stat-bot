import asyncio

from telegram.error import BadRequest, TimedOut

import bot


class FakeReactionMessage:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    async def set_reaction(self, **kwargs):
        self.calls.append(kwargs)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_set_saved_reaction_retries_after_timeout(monkeypatch):
    message = FakeReactionMessage([TimedOut(), True])
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)

    result = asyncio.run(bot.set_saved_reaction(message))

    assert result is True
    assert len(message.calls) == 2
    assert sleeps == [1.0]
    assert message.calls[0]["read_timeout"] == 5.0
    assert message.calls[0]["reaction"][0].emoji == "🏆"


def test_set_saved_reaction_stops_after_three_network_failures(monkeypatch):
    message = FakeReactionMessage([TimedOut(), TimedOut(), TimedOut()])
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)

    result = asyncio.run(bot.set_saved_reaction(message))

    assert result is False
    assert len(message.calls) == 3
    assert sleeps == [1.0, 3.0]


def test_set_saved_reaction_does_not_retry_non_network_error(monkeypatch):
    message = FakeReactionMessage([BadRequest("reaction is not allowed")])
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)

    result = asyncio.run(bot.set_saved_reaction(message))

    assert result is False
    assert len(message.calls) == 1
    assert sleeps == []
