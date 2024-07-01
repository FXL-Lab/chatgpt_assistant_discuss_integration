# import necessary libraries and modules
import json
import logging
import os
import time

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from openai import OpenAI

_logger = logging.getLogger(__name__)

class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    enable_chatgpt_assistant_response = fields.Boolean(
        string="Enable ChatGPT Assistant Response",
        help="Check this box to enable ChatGPT Assistant to respond to messages on Discuss app and website livechat",
        config_parameter="chatgpt_assistant_discuss_integration.enable_chatgpt_assistant_response",
        default=True
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
    _inherit = 'mail.channel'

    enable_chatgpt_assistant_response = fields.Boolean(
        string="Enable ChatGPT Assistant Response in this Channel",
        help="Check this box to enable ChatGPT assistant to respond to messages in this channel",
        default=True,
    )

    chatgpt_message_text = fields.Char(default=None, store=False)
    should_generate_chatgpt_response = fields.Boolean(default=False, store=False)

    @api.model
    def create_chatgpt_assistant_channel(self):
        partner_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.partner_chatgpt")
        user_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.user_chatgpt")
        channel = self.create({
            'name': 'ChatGPT Assistant',
            'channel_type': 'chat',
            'public': 'public',
            'channel_partner_ids': [(4, partner_chatgpt.id)],
            'channel_user_ids': [(4, user_chatgpt.id)],
            'enable_chatgpt_assistant_response': True,
        })
        return channel

    @api.model
    def _onchange_or_create(self, vals):
        if vals.get('public') == 'public':
            channel = self.create_chatgpt_assistant_channel()
            return channel
        return super(Channel, self)._onchange_or_create(vals)

    def _message_post_after_hook(self, message, msg_vals):
        result = super(Channel, self)._message_post_after_hook(message, msg_vals=msg_vals)

        self.chatgpt_message_text = None
        self.should_generate_chatgpt_response = False

        config_parameter = self.env['ir.config_parameter'].sudo()
        global_enable_chatgpt_assistant_response = config_parameter.get_param(
            'chatgpt_assistant_discuss_integration.enable_chatgpt_assistant_response'
        )

        if not global_enable_chatgpt_assistant_response or not self.enable_chatgpt_assistant_response:
            return result

        prompt = msg_vals.get('body')
        if not prompt:
            return result

        partner_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.partner_chatgpt")
        author_id = msg_vals.get('author_id')

        self.should_generate_chatgpt_response = author_id != partner_chatgpt.id

        if self.should_generate_chatgpt_response:
            try:
                self.chatgpt_message_text = self._get_chatgpt_response(prompt=prompt)
            except Exception as e:
                _logger.error(e)
                raise ValidationError(e)

        return result

    def _notify_thread(self, message, msg_vals, **kwargs):
        try:
            rdata = super(Channel, self)._notify_thread(message, msg_vals=msg_vals, **kwargs)
        except Exception as e:
            _logger.error(e)
            return {}

        if not self.should_generate_chatgpt_response or not self.chatgpt_message_text:
            return rdata

        user_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.user_chatgpt")
        self.with_user(user_chatgpt).sudo().message_post(
            body=self.chatgpt_message_text,
            message_type='comment',
            subtype_xmlid='mail.mt_comment'
        )

        return rdata

    def _get_chatgpt_response(self, prompt):
        config_parameter = self.env['ir.config_parameter'].sudo()
        chatgpt_api_key = config_parameter.get_param('chatgpt_assistant_discuss_integration.chatgpt_api_key')
        assistant_id = config_parameter.get_param('chatgpt_assistant_discuss_integration.assistant_id')
        try:
            client = OpenAI(api_key=chatgpt_api_key)
            thread_id = None
            thread_dict = {}
            chatgpt_thread_dict = os.getenv('CHATGPT_THREAD_DICT')
            if chatgpt_thread_dict:
                thread_dict = json.loads(chatgpt_thread_dict)
                thread_id = thread_dict.get(str(self.id))
            if not thread_id:
                thread = client.beta.threads.create()
                thread_id = thread.id
                thread_dict[str(self.id)] = thread_id
                os.environ['CHATGPT_THREAD_DICT'] = json.dumps(thread_dict)
            try:
                client.beta.threads.messages.create(
                    thread_id=thread_id,
                    role="user",
                    content=prompt,
                )
            except Exception as e:
                _logger.error(e)
                return ""
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
            if run.status == 'completed':
                messages = client.beta.threads.messages.list(
                    thread_id=thread_id
                )
                return messages.data[0].content[0].text.value
            else:
                _logger.error(run.status)
                raise RuntimeError(run.status)
        except Exception as e:
            _logger.error(e)
            raise RuntimeError(_(e))
