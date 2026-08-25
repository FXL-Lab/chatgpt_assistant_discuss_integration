# -*- coding: utf-8 -*-

import json
import logging
import re
import time
import markdown

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from openai import OpenAI
from markupsafe import Markup

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    enable_chatgpt_assistant_response = fields.Boolean(
        string="Enable ChatGPT Assistant Response",
        help="Check this box to enable ChatGPT Assistant to respond to messages on Discuss app and website livechat",
        config_parameter="chatgpt_assistant_discuss_integration.enable_chatgpt_assistant_response",
        default=False
    )
    chatgpt_api_key = fields.Char(
        string="API Key",
        help="Provide ChatGPT API key here",
        config_parameter="chatgpt_assistant_discuss_integration.chatgpt_api_key"
    )
    prompt_id = fields.Char(
        string="Prompt ID",
        help="Provide the OpenAI dashboard prompt ID used for responses",
        config_parameter="chatgpt_assistant_discuss_integration.prompt_id"
    )


class Channel(models.Model):
    _inherit = 'discuss.channel'

    # These field will not store in the database, it is only used to store some temporary value
    # to send notification correctly to the livechat and admin panel
    chatgpt_message_text = fields.Char(default=None, store=False)
    should_generate_chatgpt_response = fields.Boolean(default=False, store=False)

    # enable/disable ChatGPT assistant response in specific channel
    enable_chatgpt_assistant_response = fields.Boolean(
        string="Enable ChatGPT assistant response in this channel",
        help="Check this box to enable ChatGPT assistant to respond to messages this channel",
        default=True,
    )
    
    # Store conversation IDs per session/user for OpenAI Responses API context.
    chatgpt_conversation_sessions = fields.Text(
        string="ChatGPT Conversation Sessions",
        help="JSON mapping of session/user IDs to OpenAI conversation IDs",
        default='{}',
    )
    
    # Track which conversations were started by ChatGPT to maintain continuity
    chatgpt_active_conversations = fields.Text(
        string="ChatGPT Active Conversations",
        help="JSON mapping of session/user IDs to track if ChatGPT started the conversation",
        default='{}',
    )

    def _message_post_after_hook(self, message, msg_vals):
        result = super(Channel, self)._message_post_after_hook(message, msg_vals=msg_vals)

        self.chatgpt_message_text = None
        self.should_generate_chatgpt_response = False

        config_parameter = self.env['ir.config_parameter'].sudo()
        enable_chatgpt_assistant_response = config_parameter.get_param(
            'chatgpt_assistant_discuss_integration.enable_chatgpt_assistant_response'
        )
        if not enable_chatgpt_assistant_response or not self.enable_chatgpt_assistant_response:
            self.should_generate_chatgpt_response = False
            return result
        prompt = msg_vals.get('body')
        if not prompt:
            self.should_generate_chatgpt_response = False
            return result

        # Check for various session ending patterns
        body = msg_vals.get('body', '') # left the conversation.
        if (body.endswith('has left the conversation.') or 
            body.endswith('has ended the conversation.') or
            body.endswith('session has ended.') or
            'conversation closed' in body.lower() or
            'left the conversation.' in body.lower() or
            'img class="o_livechat_emoji_rating"' in body.lower() or
            'chat ended' in body.lower()):
            self.should_generate_chatgpt_response = False
            _logger.info(f"Session ending detected: {body}")
            # Reset the conversation for this specific session when user leaves
            session_key = self._get_session_key(msg_vals)
            self.reset_chatgpt_conversation(session_key=session_key)
            return result
        if isinstance(msg_vals.get('body'), Markup) and str(msg_vals.get('body')).startswith('<p>Rating:'):
            self.should_generate_chatgpt_response = False
            _logger.info("Message is a rating")
            # Reset the conversation after rating (conversation typically ends after rating)
            session_key = self._get_session_key(msg_vals)
            self.reset_chatgpt_conversation(session_key=session_key)
            return result

        if self.channel_type == 'livechat':
            if self.env['im_livechat.channel'].browse(self.livechat_channel_id.id).enable_chatgpt_assistant_response_channel == False:
                self.should_generate_chatgpt_response = False
                _logger.info("Livechat channel is disabled for ChatGPT assistant response")
                return result
            else:
                prompt_id = self.env['im_livechat.channel'].browse(self.livechat_channel_id.id).prompt_id
                _logger.info("Livechat channel is enabled for ChatGPT assistant response")
        else:
            _logger.info("Channel is not livechat")
            self.should_generate_chatgpt_response = False
            return result

        chatgpt_channel_id = self.env.ref('chatgpt_assistant_discuss_integration.channel_chatgpt')
        partner_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.partner_chatgpt")
        author_id = msg_vals.get('author_id')
        chatgpt_name = str(partner_chatgpt.name or '') + ', '

        is_chatgpt_private_channel = (
                author_id != partner_chatgpt.id
                and (chatgpt_name in msg_vals.get('record_name', '') or 'ChatGPT,' in msg_vals.get('record_name', ''))
                and self.channel_type == 'chat'
        )

        is_chatgpt_public_channel = (
                author_id != partner_chatgpt.id
                and msg_vals.get('model', '') == 'discuss.channel'
                and msg_vals.get('res_id', 0) == chatgpt_channel_id.id
        )

        # All operator availability and handoff logic is now handled in _should_chatgpt_respond()
        session_key = self._get_session_key(msg_vals) if self.channel_type == 'livechat' else None

        should_chatgpt_respond_livechat = (
                author_id != partner_chatgpt.id
                and (not self.env.user
                     or (
                             not self.env.user.has_group('im_livechat.im_livechat_group_user')
                             and not self.env.user.has_group('im_livechat.im_livechat_group_manager')
                     )
                     )
                and self.channel_type == 'livechat'
                and self._should_chatgpt_respond(msg_vals)  # Use unified handoff logic
        )

        self.should_generate_chatgpt_response = (
                is_chatgpt_private_channel
                or is_chatgpt_public_channel
                or should_chatgpt_respond_livechat
        )

        if self.should_generate_chatgpt_response:
            try:
                self.chatgpt_message_text = self._get_chatgpt_response(prompt=prompt, prompt_id=prompt_id, msg_vals=msg_vals)
            except Exception as e:
                _logger.error(f"message_post_after_hook error: {e}")
                # Set friendly error message instead of raising
                self.chatgpt_message_text = "Sorry, I'm experiencing technical difficulties. Please try again later."

        return result

    def _get_session_key(self, msg_vals):
        """Generate a unique session key for thread management"""
        if self.channel_type == 'livechat':
            # For livechat, use the channel UUID or anonymous user session
            # This ensures each livechat session gets its own thread
            session_key = f"livechat_{self.uuid or self.id}"
            # If there's a specific visitor/customer, include that info
            if hasattr(self, 'livechat_visitor_id') and self.livechat_visitor_id:
                session_key = f"livechat_{self.livechat_visitor_id.id}_{self.id}"
        else:
            # For regular channels, use author_id to separate conversations per user
            author_id = msg_vals.get('author_id')
            session_key = f"channel_{self.id}_user_{author_id}"
        return session_key

    def _get_conversation_for_session(self, session_key, client):
        """Get or create an OpenAI conversation for a specific session."""
        try:
            conversation_sessions = json.loads(self.chatgpt_conversation_sessions or '{}')
        except (json.JSONDecodeError, TypeError):
            conversation_sessions = {}
        
        conversation_id = conversation_sessions.get(session_key)
        
        if not conversation_id:
            conversation = client.conversations.create()
            conversation_id = conversation.id
            conversation_sessions[session_key] = conversation_id
            self.sudo().write({'chatgpt_conversation_sessions': json.dumps(conversation_sessions)})
            _logger.info(f"Created new OpenAI conversation {conversation_id} for session {session_key} in channel {self.id}")
        else:
            _logger.info(f"Using existing OpenAI conversation {conversation_id} for session {session_key} in channel {self.id}")
        
        return conversation_id, conversation_sessions

    def _replace_invalid_conversation(self, session_key, conversation_sessions, client):
        """Replace a deleted or invalid OpenAI conversation for a session."""
        conversation = client.conversations.create()
        conversation_id = conversation.id
        conversation_sessions[session_key] = conversation_id
        self.sudo().write({'chatgpt_conversation_sessions': json.dumps(conversation_sessions)})
        _logger.warning(f"Created replacement conversation {conversation_id} for session {session_key} in channel {self.id}")
        return conversation_id

    def _notify_thread(self, message, msg_vals, **kwargs):
        try:
            rdata = super(Channel, self)._notify_thread(message, msg_vals=msg_vals, **kwargs)
        except Exception as e:
            _logger.error(e)
            return {}

        author_id = msg_vals.get('author_id')
        partner_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.partner_chatgpt")
        
        # Check if a human operator is sending a message in a livechat channel
        if (self.channel_type == 'livechat' and 
            author_id != partner_chatgpt.id and 
            self.env.user and 
            (self.env.user.has_group('im_livechat.im_livechat_group_user') or 
             self.env.user.has_group('im_livechat.im_livechat_group_manager'))):
            # A human operator is responding, hand off the conversation
            session_key = self._get_session_key(msg_vals)
            self._handoff_conversation_to_human(session_key)

        # If we have a ChatGPT response ready, post it
        if not self.should_generate_chatgpt_response or not self.chatgpt_message_text:
            return rdata
        
        user_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.user_chatgpt")
        chatgpt_name = str(partner_chatgpt.name or '') + ', '
        chatgpt_channel_id = self.env.ref('chatgpt_assistant_discuss_integration.channel_chatgpt')

        is_chatgpt_private_channel = (
                author_id != partner_chatgpt.id
                and (chatgpt_name in msg_vals.get('record_name', '') or 'ChatGPT,' in msg_vals.get('record_name', ''))
                and self.channel_type == 'chat'
        )

        is_chatgpt_public_channel = (
                author_id != partner_chatgpt.id
                and msg_vals.get('model', '') == 'discuss.channel'
                and msg_vals.get('res_id', 0) == chatgpt_channel_id.id
        )

        # Post the response based on channel type
        if is_chatgpt_private_channel:
            self.with_user(user_chatgpt).message_post(
                body=Markup(self._linkify_text(self.chatgpt_message_text)),
                message_type='comment',
                subtype_xmlid='mail.mt_comment'
            )
        elif is_chatgpt_public_channel:
            chatgpt_channel_id.with_user(user_chatgpt).message_post(
                body=Markup(self._linkify_text(self.chatgpt_message_text)),
                message_type='comment',
                subtype_xmlid='mail.mt_comment'
            )
        elif self.channel_type == 'livechat':
            # For livechat, always post if we generated a response (including error messages)
            self.with_user(user_chatgpt).sudo().message_post(
                body=Markup(self._linkify_text(self.chatgpt_message_text)),
                message_type='comment',
                subtype_xmlid='mail.mt_comment'
            )

        return rdata

    def _linkify_text(self, text):
        """Convert URLs and markdown syntax to HTML using the markdown library."""
        if not text:
            return text
        
        # Configure markdown with minimal extensions for livechat
        md = markdown.Markdown(extensions=[
            'nl2br',        # Convert newlines to <br>
        ])
        
        # Convert markdown to HTML
        html_text = md.convert(text)
        
        # Ensure all <a> tags have target="_blank" and security attributes
        def add_target_blank_to_links(text):
            # Pattern to find <a> tags
            link_pattern = r'<a\s+([^>]*?)href\s*=\s*["\']([^"\']*)["\']([^>]*?)>'
            
            def replace_link(match):
                before_href = match.group(1)
                url = match.group(2)
                after_href = match.group(3)
                
                # Check if target="_blank" is already present
                if 'target=' not in before_href + after_href:
                    # Add target="_blank" and security attributes
                    return f'<a {before_href}href="{url}"{after_href} target="_blank" rel="noreferrer noopener">'
                else:
                    # Link already has target attribute, just ensure it's _blank
                    full_tag = f'<a {before_href}href="{url}"{after_href}>'
                    # Replace any existing target with _blank
                    full_tag = re.sub(r'target\s*=\s*["\'][^"\']*["\']', 'target="_blank"', full_tag)
                    # Add security attributes if not present
                    if 'rel=' not in full_tag:
                        full_tag = full_tag[:-1] + ' rel="noreferrer noopener">'
                    return full_tag
            
            return re.sub(link_pattern, replace_link, text)
        
        # Apply target="_blank" to markdown-generated links
        html_text = add_target_blank_to_links(html_text)
        
        # Phone number linkification
        def replace_phone_numbers(text):
            # Split by HTML tags to avoid processing content inside tags
            parts = re.split(r'(<[^>]*>)', text)
            result_parts = []
            
            # Pattern for international phone numbers with + prefix
            # Matches: +421 907 187 800, +1 (555) 123-4567, +44-20-1234-5678, etc.
            # Requires at least 7 digits total (minimum phone number length)
            phone_pattern = r'\+\d{1,4}[\s\-\.\(\)]*(?:\d[\s\-\.\(\)]*){6,14}\d'
            
            for i, part in enumerate(parts):
                # Only process text parts (odd indices), not HTML tags
                if i % 2 == 0:
                    def replace_phone(match):
                        phone_display = match.group(0)
                        # Strip all formatting for the tel: URI (keep only + and digits)
                        phone_uri = re.sub(r'[^\d+]', '', phone_display)
                        return f'<a href="tel:{phone_uri}">{phone_display}</a>'
                    
                    part = re.sub(phone_pattern, replace_phone, part)
                
                result_parts.append(part)
            
            return ''.join(result_parts)
        
        # Apply phone number linkification
        html_text = replace_phone_numbers(html_text)
        
        # Additional URL linkification for URLs not in markdown links
        # This handles plain URLs that aren't already in [text](url) format
        def replace_urls_not_in_html(text):
            # Split by HTML tags to avoid processing content inside tags
            parts = re.split(r'(<[^>]*>)', text)
            result_parts = []
            
            url_pattern = r'(?<!href=["\'])(?<!href=")(?<!href=\')(?:https?://|www\.)(?:[a-zA-Z0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
            
            for i, part in enumerate(parts):
                if i % 2 == 0:  # Not an HTML tag
                    def make_link(match):
                        url = match.group(0)
                        href = url if url.startswith(('http://', 'https://')) else 'http://' + url
                        return f'<a href="{href}" target="_blank" rel="noreferrer noopener">{url}</a>'
                    
                    part = re.sub(url_pattern, make_link, part)
                result_parts.append(part)
            
            return ''.join(result_parts)
        
        # Apply additional URL processing
        processed_text = replace_urls_not_in_html(html_text)
        
        return processed_text

    def _get_chatgpt_response(self, prompt, prompt_id=None, msg_vals=None):
        config_parameter = self.env['ir.config_parameter'].sudo()
        chatgpt_api_key = config_parameter.get_param('chatgpt_assistant_discuss_integration.chatgpt_api_key')
        if not prompt_id:
            prompt_id = config_parameter.get_param('chatgpt_assistant_discuss_integration.prompt_id')

        if not prompt_id:
            _logger.error("No OpenAI prompt ID configured")
            return "The chat assistant is not configured. Please contact an administrator."
        
        try:
            client = OpenAI(api_key=chatgpt_api_key)
            session_key = self._get_session_key(msg_vals)
            conversation_id, conversation_sessions = self._get_conversation_for_session(session_key, client)

            max_retries = 2
            
            for retry in range(max_retries):
                try:
                    response = client.responses.create(
                        prompt={'id': prompt_id},
                        input=[{'role': 'user', 'content': prompt}],
                        conversation=conversation_id,
                    )
                    if response.status != 'completed' or not response.output_text:
                        _logger.error(f"Response status error for session {session_key}: {response.status}")
                        raise RuntimeError(response.error.message if response.error else f"Unexpected response status: {response.status}")

                    message_text = response.output_text
                    _logger.info(f"ChatGPT response for session {session_key}: {message_text}")
                    self._mark_chatgpt_conversation_active(session_key)
                    return message_text
                            
                except Exception as e:
                    if "conversation" in str(e).lower() and ("not found" in str(e).lower() or "invalid" in str(e).lower()):
                        _logger.warning(f"Conversation {conversation_id} is invalid for session {session_key}, creating a replacement")
                        conversation_id = self._replace_invalid_conversation(session_key, conversation_sessions, client)
                        continue
                    if "rate_limit" in str(e).lower() and retry < max_retries - 1:
                        wait_time = 10
                        _logger.warning(f"Rate limit error during API call for session {session_key}, waiting {wait_time}s before retry {retry + 1}/{max_retries}")
                        time.sleep(wait_time)
                        continue
                    elif "rate_limit" in str(e).lower():
                        # Final retry exhausted for rate limit
                        _logger.error(f"Rate limit exceeded during API call after {max_retries} retries for session {session_key}")
                        return "I'm receiving too many requests right now. Please wait a moment and send your message again."
                    raise
        
        except Exception as e:
            _logger.error(f"_get_chatgpt_response error for session {session_key}: {e}")
            # Return friendly message instead of raising error
            return "Sorry, I'm having trouble responding right now. Please try again in a moment."

    def reset_chatgpt_conversation(self, session_key=None):
        """Reset the ChatGPT conversation(s) for this channel."""
        if session_key:
            try:
                conversation_sessions = json.loads(self.chatgpt_conversation_sessions or '{}')
                if session_key in conversation_sessions:
                    removed_conversation = conversation_sessions.pop(session_key)
                    self.sudo().write({'chatgpt_conversation_sessions': json.dumps(conversation_sessions)})
                    _logger.info(f"Reset ChatGPT conversation {removed_conversation} for session {session_key} in channel {self.id}")
                    return True
                else:
                    _logger.warning(f"No conversation found for session {session_key} in channel {self.id}")
                    return False
            except (json.JSONDecodeError, TypeError):
                _logger.error(f"Invalid conversation sessions data in channel {self.id}")
                return False
        else:
            conversation_count = 0
            try:
                conversation_sessions = json.loads(self.chatgpt_conversation_sessions or '{}')
                conversation_count = len(conversation_sessions)
            except (json.JSONDecodeError, TypeError):
                pass
            
            self.sudo().write({'chatgpt_conversation_sessions': '{}'})
            
            _logger.info(f"Reset all {conversation_count} ChatGPT conversations for channel {self.id}")
            return True

    def _is_chatgpt_conversation_active(self, session_key):
        """Check if ChatGPT has an active conversation for the given session"""
        if not session_key:
            return False
        try:
            active_conversations = json.loads(self.chatgpt_active_conversations or '{}')
            return active_conversations.get(session_key, False)
        except (json.JSONDecodeError, TypeError):
            return False

    def _mark_chatgpt_conversation_active(self, session_key):
        """Mark a conversation as being actively handled by ChatGPT"""
        if not session_key:
            return
        try:
            active_conversations = json.loads(self.chatgpt_active_conversations or '{}')
            active_conversations[session_key] = True
            self.sudo().write({'chatgpt_active_conversations': json.dumps(active_conversations)})
            _logger.info(f"Marked ChatGPT conversation active for session {session_key} in channel {self.id}")
        except (json.JSONDecodeError, TypeError):
            # If there's an error, start fresh
            self.sudo().write({'chatgpt_active_conversations': json.dumps({session_key: True})})

    def _has_human_operator_participated(self):
        """
        Check if any human operator has already sent messages in this conversation.
        Returns True if a human operator has ever written a message in this channel.
        Optimized to return quickly once a human operator is found.
        """
        if self.channel_type != 'livechat':
            return False
            
        partner_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.partner_chatgpt")
        
        # Search for messages from human operators (users with livechat groups, excluding ChatGPT)
        # Order by date DESC to check most recent messages first
        human_operator_messages = self.env['mail.message'].search([
            ('res_id', '=', self.id),
            ('model', '=', 'discuss.channel'),
            ('author_id', '!=', partner_chatgpt.id),
            ('message_type', '=', 'comment'),
        ], order='date desc')
        
        # Check if any of these messages are from users with operator permissions
        for message in human_operator_messages:
            if message.author_id and message.author_id.user_ids:
                user = message.author_id.user_ids[0]  # Get the first user associated with this partner
                if (user.has_group('im_livechat.im_livechat_group_user') or 
                    user.has_group('im_livechat.im_livechat_group_manager')):
                    _logger.info(f"Found human operator message from user {user.name} in channel {self.id} - ChatGPT will not respond")
                    return True
        
        _logger.debug(f"No human operator messages found in channel {self.id} - ChatGPT may respond")
        return False

    def _should_chatgpt_respond(self, msg_vals):
        """
        Determine if ChatGPT should respond based on operator availability and conversation state.
        
        Logic:
        1. If a human operator has ever participated in this conversation -> ChatGPT never responds
        2. If no human operators are available -> ChatGPT responds
        3. If ChatGPT conversation is already active -> ChatGPT continues responding
        4. If human operators are available AND no active ChatGPT conversation -> ChatGPT stays silent
        """
        if self.channel_type != 'livechat' or not self.livechat_channel_id:
            return False
            
        # MOST IMPORTANT: If a human operator has ever participated, ChatGPT should never respond
        if self._has_human_operator_participated():
            session_key = self._get_session_key(msg_vals)
            _logger.debug(f"Human operator has participated in channel {self.id}, session {session_key}. ChatGPT will not respond.")
            return False
            
        # Check if there are available human operators
        livechat_channel = self.env['im_livechat.channel'].browse(self.livechat_channel_id.id)
        user_chatgpt_ref = self.env.ref("chatgpt_assistant_discuss_integration.user_chatgpt")
        available_operators = livechat_channel.available_operator_ids.filtered(lambda u: u.id != user_chatgpt_ref.id)
        has_available_operators = len(available_operators) > 0
        
        # Check if ChatGPT conversation is already active for this session
        session_key = self._get_session_key(msg_vals)
        chatgpt_conversation_active = self._is_chatgpt_conversation_active(session_key)
        
        # ChatGPT should respond if:
        # 1. No human operators are available, OR
        # 2. ChatGPT conversation is already active (continue until human takes over)
        should_respond = not has_available_operators or chatgpt_conversation_active
        
        _logger.debug(f"ChatGPT response decision for session {session_key}: "
                     f"has_operators={has_available_operators}, "
                     f"conversation_active={chatgpt_conversation_active}, "
                     f"should_respond={should_respond}")
        
        return should_respond

    def _handoff_conversation_to_human(self, session_key):
        """Hand off a ChatGPT conversation to a human operator"""
        if not session_key:
            return
        try:
            active_conversations = json.loads(self.chatgpt_active_conversations or '{}')
            if session_key in active_conversations:
                active_conversations.pop(session_key)
                self.sudo().write({'chatgpt_active_conversations': json.dumps(active_conversations)})
                _logger.info(f"Handed off ChatGPT conversation to human for session {session_key} in channel {self.id}")
                
                # Optionally, reset the ChatGPT conversation after a handoff.
                # self.reset_chatgpt_conversation(session_key=session_key)
        except (json.JSONDecodeError, TypeError):
            _logger.error(f"Error during conversation handoff for session {session_key} in channel {self.id}")
