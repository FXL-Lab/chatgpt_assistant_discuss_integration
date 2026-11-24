# -*- coding: utf-8 -*-

import os

from odoo import _
from odoo.http import request, route
from odoo.addons.im_livechat.controllers.attachment import LivechatAttachmentController
from odoo.addons.im_livechat.controllers.main import LivechatController

from ..file_support import CHAT_ATTACHMENT_MIME_TYPES, MAX_CHAT_ATTACHMENT_BYTES


class ChatGPTLivechatController(LivechatController):

    @route()
    def get_session(
        self,
        channel_id,
        anonymous_name,
        previous_operator_id=None,
        chatbot_script_id=None,
        persisted=True,
        **kwargs,
    ):
        result = super().get_session(
            channel_id,
            anonymous_name,
            previous_operator_id=previous_operator_id,
            chatbot_script_id=chatbot_script_id,
            persisted=persisted,
            **kwargs,
        )
        if not persisted and result and (channel_infos := result.get('discuss.channel')):
            allow_public_upload = request.env['im_livechat.channel'].sudo().browse(
                channel_id
            ).chatgpt_allow_public_upload
            if isinstance(channel_infos, dict):
                channel_infos['allow_public_upload'] = allow_public_upload
            else:
                for channel_info in channel_infos:
                    channel_info['allow_public_upload'] = allow_public_upload
        return result


class ChatGPTLivechatAttachmentController(LivechatAttachmentController):

    @route()
    def mail_attachment_upload(self, ufile, thread_id, thread_model, is_pending=False, **kwargs):
        if thread_model == 'discuss.channel':
            channel = request.env['discuss.channel'].sudo().browse(int(thread_id)).exists()
            if channel and channel.channel_type == 'livechat':
                extension = os.path.splitext(ufile.filename or '')[1].lower()
                if extension not in CHAT_ATTACHMENT_MIME_TYPES:
                    return request.make_json_response({
                        'error': _('Only PDF, JPEG, PNG, and WebP files are supported in livechat.')
                    })

                content = ufile.stream.read(MAX_CHAT_ATTACHMENT_BYTES + 1)
                ufile.stream.seek(0)
                if len(content) > MAX_CHAT_ATTACHMENT_BYTES:
                    return request.make_json_response({
                        'error': _('Livechat files must be 10 MB or smaller.')
                    })

        return super().mail_attachment_upload(ufile, thread_id, thread_model, is_pending, **kwargs)
