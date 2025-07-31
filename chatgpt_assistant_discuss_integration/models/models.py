# -*- coding: utf-8 -*-

import json
import logging
import time

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
    assistant_id = fields.Char(
        string="Assistant ID",
        help="Provide Assistant ID here",
        config_parameter="chatgpt_assistant_discuss_integration.assistant_id"
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
    
    # Store thread IDs per session/user for proper conversation management
    chatgpt_thread_sessions = fields.Text(
        string="ChatGPT Thread Sessions",
        help="JSON mapping of session/user IDs to OpenAI thread IDs",
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
            # Reset the thread for this specific session when user leaves
            session_key = self._get_session_key(msg_vals)
            self.reset_chatgpt_thread(session_key=session_key)
            return result
        if isinstance(msg_vals.get('body'), Markup) and str(msg_vals.get('body')).startswith('<p>Rating:'):
            self.should_generate_chatgpt_response = False
            _logger.info("Message is a rating")
            # Reset thread after rating (conversation typically ends after rating)
            session_key = self._get_session_key(msg_vals)
            self.reset_chatgpt_thread(session_key=session_key)
            return result

        if self.channel_type == 'livechat':
            if self.env['im_livechat.channel'].browse(self.livechat_channel_id.id).enable_chatgpt_assistant_response_channel == False:
                self.should_generate_chatgpt_response = False
                _logger.info("Livechat channel is disabled for ChatGPT assistant response")
                return result
            else:
                assistant_id = self.env['im_livechat.channel'].browse(self.livechat_channel_id.id).assistant_id
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

        should_chatgpt_respond_livechat = (
                author_id != partner_chatgpt.id
                and (not self.env.user
                     or (
                             not self.env.user.has_group('im_livechat.im_livechat_group_user')
                             and not self.env.user.has_group('im_livechat.im_livechat_group_manager')
                     )
                     )
                and self.channel_type == 'livechat'
        )

        self.should_generate_chatgpt_response = (
                is_chatgpt_private_channel
                or is_chatgpt_public_channel
                or should_chatgpt_respond_livechat
        )

        try:
            if self.should_generate_chatgpt_response:
                self.chatgpt_message_text = self._get_chatgpt_response(prompt=prompt, assistant_id=assistant_id, msg_vals=msg_vals)
        except Exception as e:
            _logger.error(f"message_post_after_hook: {e}")
            raise ValidationError(e)

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

    def _get_thread_for_session(self, session_key, client):
        """Get or create an OpenAI thread for a specific session"""
        try:
            thread_sessions = json.loads(self.chatgpt_thread_sessions or '{}')
        except (json.JSONDecodeError, TypeError):
            thread_sessions = {}
        
        thread_id = thread_sessions.get(session_key)
        
        if not thread_id:
            # Create new thread for this session
            thread = client.beta.threads.create()
            thread_id = thread.id
            thread_sessions[session_key] = thread_id
            self.sudo().write({'chatgpt_thread_sessions': json.dumps(thread_sessions)})
            _logger.info(f"Created new OpenAI thread {thread_id} for session {session_key} in channel {self.id}")
        else:
            _logger.info(f"Using existing OpenAI thread {thread_id} for session {session_key} in channel {self.id}")
        
        return thread_id, thread_sessions

    def _cleanup_invalid_thread(self, session_key, thread_sessions, client):
        """Remove invalid thread and create a new one"""
        thread = client.beta.threads.create()
        thread_id = thread.id
        thread_sessions[session_key] = thread_id
        self.sudo().write({'chatgpt_thread_sessions': json.dumps(thread_sessions)})
        _logger.warning(f"Created replacement thread {thread_id} for session {session_key} in channel {self.id}")
        return thread_id

    def _notify_thread(self, message, msg_vals, **kwargs):
        try:
            rdata = super(Channel, self)._notify_thread(message, msg_vals=msg_vals, **kwargs)
        except Exception as e:
            _logger.error(e)
            return {}

        if not self.should_generate_chatgpt_response or not self.chatgpt_message_text:
            return rdata

        author_id = msg_vals.get('author_id')
        partner_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.partner_chatgpt")
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

        should_chatgpt_respond_livechat = (
                author_id != partner_chatgpt.id
                and (not self.env.user
                     or (
                             not self.env.user.has_group('im_livechat.im_livechat_group_user')
                             and not self.env.user.has_group('im_livechat.im_livechat_group_manager')
                     )
                     )
                and self.channel_type == 'livechat'
        )

        user_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.user_chatgpt")

        if (
                is_chatgpt_private_channel
        ):
            self.with_user(user_chatgpt).message_post(
                body=self.chatgpt_message_text,
                message_type='comment',
                subtype_xmlid='mail.mt_comment'
            )
        elif (
                is_chatgpt_public_channel
        ):
            chatgpt_channel_id.with_user(user_chatgpt).message_post(
                body=self.chatgpt_message_text,
                message_type='comment',
                subtype_xmlid='mail.mt_comment'
            )
        elif (
                should_chatgpt_respond_livechat
        ):
            self.with_user(user_chatgpt).sudo().message_post(
                body=self.chatgpt_message_text,
                message_type='comment',
                subtype_xmlid='mail.mt_comment'
            )

        return rdata

    def _get_chatgpt_response(self, prompt, assistant_id, msg_vals):
        config_parameter = self.env['ir.config_parameter'].sudo()
        chatgpt_api_key = config_parameter.get_param('chatgpt_assistant_discuss_integration.chatgpt_api_key')
        if not assistant_id:
            assistant_id = config_parameter.get_param('chatgpt_assistant_discuss_integration.assistant_id')
        
        try:
            client = OpenAI(api_key=chatgpt_api_key)
            
            # Get session-specific thread ID
            session_key = self._get_session_key(msg_vals)
            thread_id, thread_sessions = self._get_thread_for_session(session_key, client)
            
            try:
                client.beta.threads.messages.create(
                    thread_id=thread_id,
                    role="user",
                    content=prompt,
                )
            except Exception as e:
                _logger.error(f"_get_chatgpt_response error messages: {e}")
                # If thread is invalid, create a new one
                if "No thread found" in str(e) or "thread" in str(e).lower():
                    _logger.warning(f"Thread {thread_id} not found for session {session_key}, creating new thread")
                    thread_id = self._cleanup_invalid_thread(session_key, thread_sessions, client)
                    client.beta.threads.messages.create(
                        thread_id=thread_id,
                        role="user",
                        content=prompt,
                    )
                else:
                    return ""
            
            # Handle rate limiting with retries
            wait_time = 10
            for i in range(5):
                run = client.beta.threads.runs.create(
                    thread_id=thread_id,
                    assistant_id=assistant_id,
                )
                
                while run.status in ['queued', 'in_progress', 'cancelling']:
                    time.sleep(1)  # Wait for 1 second
                    run = client.beta.threads.runs.retrieve(
                        thread_id=thread_id,
                        run_id=run.id
                    )
                
                if run.status == 'failed':
                    if run.last_error and run.last_error.code == 'rate_limit_exceeded':
                        _logger.warning(f"Rate limit exceeded for session {session_key}, waiting for {wait_time} seconds")
                        time.sleep(wait_time)
                        wait_time += 5
                        continue
                    _logger.error(f"Run error for session {session_key}: {run.last_error}")
                    raise RuntimeError(run.last_error.code if run.last_error else "Unknown error")
                
                if run.status == 'completed':
                    messages = client.beta.threads.messages.list(thread_id=thread_id)
                    msg = messages.data[0].content[0].text.value
                    return msg
                else:
                    _logger.error(f"Run status error for session {session_key}: {run.status}")
                    if run.last_error:
                        _logger.error(f"Run error: {run.last_error}")
                        raise RuntimeError(run.last_error.code)
                    else:
                        raise RuntimeError(f"Unknown run status: {run.status}")
        
        except Exception as e:
            _logger.error(f"_get_chatgpt_response error for session {session_key}: {e}")
            raise RuntimeError('Chatbot error, please try again later.')

    def reset_chatgpt_thread(self, session_key=None):
        """Reset the ChatGPT thread(s) for this channel to start fresh conversation(s)"""
        if session_key:
            # Reset specific session thread
            try:
                thread_sessions = json.loads(self.chatgpt_thread_sessions or '{}')
                if session_key in thread_sessions:
                    removed_thread = thread_sessions.pop(session_key)
                    self.sudo().write({'chatgpt_thread_sessions': json.dumps(thread_sessions)})
                    _logger.info(f"Reset ChatGPT thread {removed_thread} for session {session_key} in channel {self.id}")
                    return True
                else:
                    _logger.warning(f"No thread found for session {session_key} in channel {self.id}")
                    return False
            except (json.JSONDecodeError, TypeError):
                _logger.error(f"Invalid thread sessions data in channel {self.id}")
                return False
        else:
            # Reset all sessions for this channel
            thread_count = 0
            try:
                thread_sessions = json.loads(self.chatgpt_thread_sessions or '{}')
                thread_count = len(thread_sessions)
            except (json.JSONDecodeError, TypeError):
                pass
            
            self.sudo().write({'chatgpt_thread_sessions': '{}'})
            
            _logger.info(f"Reset all {thread_count} ChatGPT threads for channel {self.id}")
            return True
