from telethon.tl.types import Message, DocumentAttributeSticker

def get_user_display_name(user) -> str:
    """Get user display name with fallback"""
    
    if user.username:
        return f"@{user.username}"
    elif user.first_name:
        return user.first_name
    else :
        return str(user.id)

def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def get_message_content_for_db(message: Message) -> str:
    """Extracts text or a placeholder from a message for DB logging."""
    if message.text:
        return message.text
    elif message.sticker:
        # Try to get the emoji associated with the sticker
        emoji_alt = next((attr.alt for attr in message.sticker.attributes if isinstance(attr, DocumentAttributeSticker)), "")
        return f"[sticker: {emoji_alt}]".strip()
    elif message.photo:
        return "[photo]"
    elif message.video:
        return "[video]"
    elif message.document:
        return f"[document: {message.document.mime_type}]"
    else:
        return "[unsupported media]"