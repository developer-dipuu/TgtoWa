import logging
from typing import Optional
from telethon import events
from telethon.events import StopPropagation

from src import db
from src.bot.handlers.context import BotContext
from src.core.config import DAILY_LIMIT_PREMIUM, DAILY_LIMIT_REGULAR

logger = logging.getLogger(__name__)

class AdminCommands:
    def __init__(self, ctx: BotContext):
        self.ctx = ctx

    async def add_premium_command(self, event: events.NewMessage.Event):
        """Admin command to add a premium user."""
        if not await db.is_admin(event.sender_id):
            raise StopPropagation # Silently ignore for non-admins

        user_arg = event.pattern_match.group(1)
        duration_arg = event.pattern_match.group(2)
        
        # If both are None, it means the command was likely just /addpremium
        if not user_arg and not duration_arg:
            await event.reply("ℹ️ **Usage:** `/addpremium <user_id/@username> <days>`\nOr, reply to a user's message with `/addpremium <days>`.")
            raise StopPropagation

        # Logic to handle different argument combinations
        target_user = None
        duration_days = None

        if user_arg and user_arg.isdigit() and not duration_arg:
            # Case: /addpremium <days> (with reply)
            duration_days = int(user_arg)
            target_user = await self.ctx.helpers.get_user_from_event(event, None) # Get from reply
        elif user_arg and duration_arg:
            # Case: /addpremium <user> <days>
            duration_days = int(duration_arg)
            target_user = await self.ctx.helpers.get_user_from_event(event, user_arg)
        else:
            await event.reply("ℹ️ **Invalid format.**\nUsage: `/addpremium <user_id/@username> <days>`\nOr, reply to a user's message with `/addpremium <days>`.")
            raise StopPropagation

        if not target_user:
            await event.reply("❌ **User not found.** You must specify a user by their ID/@username or by replying to their message.")
            raise StopPropagation
        
        if not duration_days or duration_days <= 0:
            await event.reply("❌ **Invalid duration.** Please provide a positive number of days.")
            raise StopPropagation
        
        if await db.is_premium(target_user.id):
            await event.reply("🤷‍♂️ This user is already premium. Use `/extendpremium` to extend their duration.")
            raise StopPropagation
            
        try:
            expiry = await db.add_premium(target_user.id, target_user.username, event.sender_id, duration_days, reason="added by admin")
        except OverflowError as e:
            logger.error(f"An error has occurred while adding {target_user.id} to premium by {event.sender_id}. Error: {e}")
            await event.reply("❌ Duration is too long.")
            raise StopPropagation
        except ValueError as e:
            logger.error(f"An error has occurred while adding {target_user.id} to premium by {event.sender_id}. Error: {e}")
            await event.reply(f"❌ Error: ```{e}```")
            raise StopPropagation
        except Exception as e:
            logger.error(f"An error has occurred while adding {target_user.id} to premium by {event.sender_id}. Error: {e}")
            await event.reply(f"❌ An error has occurred maybe this is not a valid user or the user hasn't started the bot.\n\nError: ```{e}```")
            raise StopPropagation

        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
        await event.reply(
            f"⭐ Successfully granted premium to **{full_name}** (`{target_user.id}`)!\n"
            f"Expires in: `{duration_days}` days (on `{expiry.strftime('%Y-%m-%d %H:%M')} UTC`)."
        )
        logger.info(f"User {target_user.id} granted {duration_days} days of premium by admin: {event.sender_id}")
        raise StopPropagation
    
    async def remove_premium_command(self, event: events.NewMessage.Event):
        """Admin command to remove a premium user."""
        if not await db.is_admin(event.sender_id):
            raise StopPropagation

        target_user = await self.ctx.helpers.get_user_from_event(event, event.pattern_match.group(1))
        if not target_user:
            await event.reply("ℹ️ **Usage:** `/removepremium <user_id/@username>` or reply to a user.")
            raise StopPropagation
        
        if not await db.is_premium(target_user.id):
            await event.reply("🤷‍♂️ This user does not have an active premium subscription.")
            raise StopPropagation

        try: 
            await db.remove_premium(target_user.id, event.sender_id, reason="removed by admin")
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            await event.reply(f"✅ Premium status for **{full_name}** (`{target_user.id}`) has been revoked.")
            logger.info(f"Premium of user {target_user.id} has been revoked by admin: {event.sender_id}")
        except ValueError as e:
            logger.error(f"Failed to remove premium for user {target_user.id} by admin {event.sender_id}: {e}")
            await event.reply(f"❌ An error occurred. Could not remove premium status.\n```{e}```")
        except Exception as e:
            logger.error(f"Failed to remove premium for user {target_user.id} by admin {event.sender_id}: {e}")
            await event.reply(f"❌ An error occurred. Could not remove premium status.\n```{e}```")
        raise StopPropagation

    async def extend_premium_command(self, event: events.NewMessage.Event):
        """Admin command to extend a premium user's subscription."""
        if not await db.is_admin(event.sender_id):
            raise StopPropagation
        
        user_arg = event.pattern_match.group(1)
        days_arg = event.pattern_match.group(2)

        if not user_arg or not days_arg:
             await event.reply("ℹ️ **Usage:** `/extendpremium <user_id/@username> <days>`.")
             raise StopPropagation
        
        target_user = await self.ctx.helpers.get_user_from_event(event, user_arg)
        if not target_user:
            await event.reply("❌ User not found.")
            raise StopPropagation

        if not await db.is_premium(target_user.id):
            await event.reply("🤷‍♂️ This user isn't premium. Use `/addpremium` to grant them premium first.")
            raise StopPropagation
        
        days_to_add = int(days_arg)
        try:
            new_expiry = await db.manage_premium_duration(target_user.id, event.sender_id, 'extended', days_to_add, reason="extended by admin")
        except OverflowError as e:
            await event.reply("❌ Duration is too long.")
            raise StopPropagation
        except ValueError as e:
            logger.error(f"Failed to extend premium for user {target_user.id} by admin {event.sender_id}: {e}")
            await event.reply(f"❌ An error occurred. Could not extend premium status.\n```{e}```")
            raise StopPropagation
        except Exception as e:
            logger.error(f"Failed to extend premium for user {target_user.id} by admin {event.sender_id}: {e}")
            await event.reply(f"❌ An unknown error has occurred.\n```{e}```")
            raise StopPropagation

        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()

        logger.info(f"Premium of user {target_user.id} has been extended by {days_to_add} days by admin: {event.sender_id}")
        await event.reply(
            f"✅ Extended premium for **{full_name}** by `{days_to_add}` days.\n"
            f"New expiry date: `{new_expiry.strftime('%Y-%m-%d %H:%M')}`."
        )
        raise StopPropagation

    async def deduct_premium_command(self, event: events.NewMessage.Event):
        """Admin command to deduct days from a premium user's subscription."""
        if not await db.is_admin(event.sender_id):
            raise StopPropagation

        user_arg = event.pattern_match.group(1)
        days_arg = event.pattern_match.group(2)
        
        if not user_arg or not days_arg:
             await event.reply("ℹ️ **Usage:** `/deductpremium <user_id/@username> <days>`.")
             raise StopPropagation
        
        target_user = await self.ctx.helpers.get_user_from_event(event, user_arg)
        if not target_user:
            await event.reply("❌ User not found.")
            raise StopPropagation

        full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()

        current_days_left= await db.get_premium_duration_left(target_user.id)
        if current_days_left is None:
            await event.reply("❌ User does not have an active premium subscription.")
            raise StopPropagation
        current_days_left = current_days_left.days

        if int(days_arg) > current_days_left:
            try:
                await db.remove_premium(target_user.id, event.sender_id, reason="deducted by admin | deduct > duration left")
                await event.reply(f"✅ Since **{full_name}** had only `{current_days_left + 1}` days of premium left, they have been **removed** from premium.")
                logger.info(f"Premium of user {target_user.id} has been revoked by admin: {event.sender_id}")
            except ValueError as e:
                logger.error(f"Failed to remove premium for user {target_user.id} by admin {event.sender_id}: {e}")
                await event.reply(f"❌ An error occurred. Could not remove premium status.\n```{e}```")
            except Exception as e:
                logger.error(f"Failed to remove premium for user {target_user.id} by admin {event.sender_id}: {e}")
                await event.reply(f"❌ An error occurred. Could not remove premium status.\n```{e}```")
            raise StopPropagation

        days_to_deduct = -abs(int(days_arg)) # Ensure it's a negative number
        try:
            new_expiry = await db.manage_premium_duration(target_user.id, event.sender_id, 'deducted', days_to_deduct, reason="deducted by admin")
        except OverflowError as e:
            await event.reply("❌ Duration is too long.")
            raise StopPropagation
        except ValueError as e:
            logger.error(f"Failed to deduct premium for user {target_user.id} by admin {event.sender_id}: {e}")
            await event.reply(f"❌ An error occurred. Could not deduct premium.\n```{e}````")
            raise StopPropagation
        except Exception as e:
            logger.error(f"Failed to deduct premium for user {target_user.id} by admin {event.sender_id}: {e}")
            await event.reply(f"❌ An error occurred. Could not deduct premium.\n```{e}````")
            raise StopPropagation

        
        expiry_message = f"New expiry date: `{new_expiry.strftime('%Y-%m-%d %H:%M')}`."

        await event.reply(
            f"✅ Deducted `{abs(days_to_deduct)}` days from **{full_name}**'s premium.\n{expiry_message}"
        )
        logger.info(f"Premium of user {target_user.id} has been deducted by {abs(days_to_deduct)} days by admin: {event.sender_id}")
        raise StopPropagation

    async def getstats_command(self, event: events.NewMessage.Event):
        """Admin command to get conversion stats for a specific user."""
        if not await db.is_admin(event.sender_id):
            raise StopPropagation # Only admins can use this

        target_user = await self.ctx.helpers.get_user_from_event(event, event.pattern_match.group(1))
        if not target_user:
            await event.reply("ℹ️ **Usage:** `/getstats <user_id/@username>` or reply to a user's message.")
            raise StopPropagation
        
        if await db.is_user(target_user.id):
            is_premium = await db.is_premium(target_user.id)
            
            # Get user role for display
            role = "👤 Regular User"
            if db.is_owner(target_user.id):
                role = "👑 Owner"
            elif await db.is_admin(target_user.id):
                role = "👮‍♂️ Admin"
            elif is_premium:
                role = "⭐ Premium User"
                duration_left = await db.get_premium_duration_left(target_user.id)
                if duration_left:
                    days = duration_left.days
                    hours = duration_left.seconds // 3600
                    minutes = (duration_left.seconds % 3600) // 60
                    role += f"\n⏳ **Expires in**: {days}d {hours}h {minutes}m"
            
            stats = await db.get_user_stats(target_user.id)
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            daily_usage = await db.get_daily_usage(target_user.id)
            daily_limit = DAILY_LIMIT_PREMIUM if is_premium else DAILY_LIMIT_REGULAR
            limit_str = f"{daily_usage}/{daily_limit}" if daily_limit > 0 else "Unlimited"
            
            message = (
                f"📊 **Stats for [{full_name}](tg://user?id={target_user.id})** (`{target_user.id}`)\n\n"
                f"**Status**: {role}\n"
                f"**Today's Usage**: `{limit_str}`\n\n"
                f"**Conversions Log**:\n"
                f"  • Total Requests: `{stats['total']}`\n"
                f"  • ✅ Succeeded: `{stats['succeeded']}`\n"
                f"  • ❌ Failed: `{stats['failed']}`\n"
                f"  • 🚫 Cancelled: `{stats['cancelled']}`"
            )
        else:
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            message = f"🫤 The user **[{full_name}](tg://user?id={target_user.id})** has not started the bot yet."
        
        if await db.is_banned(target_user.id):
            message += "\n\n🚫 **This user has been banned.**"
        
        logger.info(f"Stats of user {target_user.id} has been fetched by admin: {event.sender_id}")
        await event.reply(message)
        raise StopPropagation
    
    async def _parse_user_and_reason(self, event: events.NewMessage.Event) -> tuple[Optional[object], str]:
        """
        Parses a command event to extract the target user and the reason.
        Handles both replies and direct user arguments.
        """
        target_user = None
        reason = "No reason provided."
        command_text = event.text or ""

        # if its a reply 
        if event.reply_to_msg_id:
            target_user = await self.ctx.helpers.get_user_from_event(event, None)
            if target_user:
                parts = command_text.split(maxsplit=1)
                if len(parts) > 1:
                    reason = parts[1]
        else:
            # Not a reply, parse user and reason from the command text
            parts = command_text.split(maxsplit=2)
            user_arg = parts[1] if len(parts) > 1 else None
            reason = parts[2] if len(parts) > 2 else reason
            target_user = await self.ctx.helpers.get_user_from_event(event, user_arg)
        
        return target_user, reason

    # silent ban command
    async def sban_command(self, event: events.NewMessage.Event):
        """Admin command to SILENTLY ban a user."""
        if not await db.is_admin(event.sender_id):
            raise StopPropagation
        
        target_user, reason = await self._parse_user_and_reason(event)

        if not target_user:
            await event.reply("ℹ️ **Usage:** `/sban <user_id/@username> [reason]` or reply to a user.")
            raise StopPropagation

        if db.is_owner(target_user.id) or await db.is_admin(target_user.id):
            await event.reply("❌ Admins and Owners cannot be banned.")
            raise StopPropagation

        if await db.is_banned(target_user.id):
            await event.reply("🤷‍♂️ This user is already banned.")
            raise StopPropagation
        
        if await db.ban_user(target_user.id, event.sender_id, reason, is_silent=True):
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            await event.reply(f"🚫 **Silently Banned {full_name}** (`{target_user.id}`).")
            logger.info(f"User {target_user.id} silently banned by admin: {event.sender_id}. Reason: {reason}")
        else:
            await event.reply(f"❌ Failed to ban `{target_user.id}`. Maybe they are already banned.")
        raise StopPropagation
    
    # notified ban command 
    async def ban_command(self, event: events.NewMessage.Event):
        """Admin command to ban a user and NOTIFY them."""
        if not await db.is_admin(event.sender_id):
            raise StopPropagation

        target_user, reason = await self._parse_user_and_reason(event)

        if not target_user:
            await event.reply("ℹ️ **Usage:** `/ban <user_id/@username> [reason]` or reply to a user.")
            raise StopPropagation

        if db.is_owner(target_user.id) or await db.is_admin(target_user.id):
            await event.reply("❌ Admins and Owners cannot be banned.")
            raise StopPropagation
        
        if await db.is_banned(target_user.id):
            await event.reply("🤷‍♂️ This user is already banned.")
            raise StopPropagation

        if await db.ban_user(target_user.id, event.sender_id, reason, is_silent=False):
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            logger.info(f"User {target_user.id} banned by admin: {event.sender_id}. Reason: {reason}")
            
            notification_status = ""
            try:
                await self.ctx.client.send_message(
                    target_user.id,
                    f"🚫 **You have been banned** from using this bot by an administrator.\n\n**Reason:** {reason}"
                )
                notification_status = "User has been notified."
            except Exception as e:
                logger.warning(f"Could not notify user {target_user.id} about their ban: {e}")
                notification_status = "Could not notify the user (they may have blocked the bot or haven't started yet)."

            await event.reply(f"🚫 **Banned {full_name}** (`{target_user.id}`).\n{notification_status}")
        else:
            await event.reply("❌ Failed to ban user. User might have already been banned.")
        raise StopPropagation

    # unban command
    async def unban_command(self, event: events.NewMessage.Event):
        """Admin command to unban a user."""
        if not await db.is_admin(event.sender_id):
            raise StopPropagation

        target_user, reason = await self._parse_user_and_reason(event)
            
        if not target_user:
            await event.reply("ℹ️ **Usage:** `/unban <user_id/@username> [reason]` or reply to a user.")
            raise StopPropagation

        if not await db.is_banned(target_user.id):
            await event.reply("🤷‍♂️ This user is not currently banned.")
            raise StopPropagation

        if await db.unban_user(target_user.id, event.sender_id, reason):
            full_name = f"{target_user.first_name} {target_user.last_name or ''}".strip()
            await event.reply(f"✅ **Unbanned {full_name}** (`{target_user.id}`). They can now use the bot again.")
            logger.info(f"User {target_user.id} unbanned by admin: {event.sender_id}. Reason: {reason}")
        else:
            await event.reply("❌ An error occurred. User might have already been unbanned.")
        raise StopPropagation
