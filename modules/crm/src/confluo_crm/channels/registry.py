"""Channel adapters by channel name, for messages that start on our side (staff
replies, booking confirmations) rather than in reply to an inbound event."""

from confluo_crm.channels.base import ChannelAdapter
from confluo_crm.channels.web import WebChatAdapter

ADAPTERS: dict[str, ChannelAdapter] = {"web": WebChatAdapter()}


def adapter_for(channel: str) -> ChannelAdapter | None:
    return ADAPTERS.get(channel)
