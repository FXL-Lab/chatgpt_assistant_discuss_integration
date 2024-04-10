# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

from openai import OpenAI
import time

import logging
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
    _inherit = 'mail.channel'

    # enable/disable ChatGPT assistant response in specific channel
    enable_chatgpt_assistant_response = fields.Boolean(
        string="Enable ChatGPT assistant response in this channel",
        help="Check this box to enable ChatGPT assistant to respond to messages this channel",
        default=True,
    )

    def _notify_thread(self, message, msg_vals=None, **kwargs):
        rdata = super(Channel, self)._notify_thread(message, msg_vals=msg_vals, **kwargs)

        config_parameter = self.env['ir.config_parameter'].sudo()
        enable_chatgpt_assistant_response = config_parameter.get_param('chatgpt_assistant_discuss_integration.enable_chatgpt_assistant_response')
        if not enable_chatgpt_assistant_response:
            return rdata

        prompt = msg_vals.get('body')
        if not prompt:
            return rdata

        chatgpt_channel_id = self.env.ref('chatgpt_assistant_discuss_integration.channel_chatgpt')
        user_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.user_chatgpt")
        partner_chatgpt = self.env.ref("chatgpt_assistant_discuss_integration.partner_chatgpt")
        author_id = msg_vals.get('author_id')
        chatgpt_name = str(partner_chatgpt.name or '') + ', '

        try:
            if (
                author_id != partner_chatgpt.id
                and (
                    chatgpt_name in msg_vals.get('record_name', '')
                    or 'ChatGPT,' in msg_vals.get('record_name', '')
                )
                and self.channel_type == 'chat'
            ):
                # Private chat with ChatGPT
                response_text = self._get_chatgpt_response(prompt=prompt)
                self.with_user(user_chatgpt).message_post(
                    body=response_text,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment'
                )
            elif (
                author_id != partner_chatgpt.id
                and msg_vals.get('model', '') == 'mail.channel'
                and msg_vals.get('res_id', 0) == chatgpt_channel_id.id
            ):
                # ChatGPT channel
                response_text = self._get_chatgpt_response(prompt=prompt)
                chatgpt_channel_id.with_user(user_chatgpt).message_post(
                    body=response_text,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment'
                )
            elif (
                not author_id
                and self.channel_type == 'livechat'
            ):
                # Livechat
                response_text = self._get_chatgpt_response(prompt=prompt)
                self.with_user(user_chatgpt).sudo().message_post(
                    body=response_text,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment'
                )
        except Exception as e:
            _logger.error(e)
            raise ValidationError(e)

        return rdata

    def _get_chatgpt_response(self, prompt):
        config_parameter = self.env['ir.config_parameter'].sudo()
        chatgpt_api_key = config_parameter.get_param('chatgpt_assistant_discuss_integration.chatgpt_api_key')
        assistant_id = config_parameter.get_param('chatgpt_assistant_discuss_integration.assistant_id')
        try:
            client = OpenAI(api_key=chatgpt_api_key)
            thread = client.beta.threads.create()
            message = client.beta.threads.messages.create(
                thread_id=thread.id,
                role="user",
                content=prompt,
            )
            run = client.beta.threads.runs.create(
                thread_id=thread.id,
                assistant_id=assistant_id,
            )
            while run.status in ['queued', 'in_progress', 'cancelling']:
                time.sleep(1)  # Wait for 1 second
                run = client.beta.threads.runs.retrieve(
                    thread_id=thread.id,
                    run_id=run.id
                )
            if run.status == 'completed':
                messages = client.beta.threads.messages.list(
                    thread_id=thread.id
                )
                return messages.data[0].content[0].text.value
            else:
                _logger.error(run.status)
                raise RuntimeError(run.status)
        except Exception as e:
            _logger.error(e)
            raise RuntimeError(_(e))
