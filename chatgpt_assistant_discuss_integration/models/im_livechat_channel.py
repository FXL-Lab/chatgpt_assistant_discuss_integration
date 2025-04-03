import logging
from odoo import api, Command, fields, models

_logger = logging.getLogger(__name__)

class ImLivechatChannel(models.Model):
    _inherit = 'im_livechat.channel'

    enable_chatgpt_assistant_response_channel = fields.Boolean(
        string="Enable ChatGPT Assistant in this channel",
        default=False,
    )
    assistant_id = fields.Char(
        string="Assistant ID",
        help="ID of the assistant to be used in this channel.",
        default=''
    )

    def _get_available_users(self):
        users = super(ImLivechatChannel, self)._get_available_users()
        enable_chatgpt = self.env['ir.config_parameter'].sudo().get_param(
            'chatgpt_assistant_discuss_integration.enable_chatgpt_assistant_response'
        )
        if not enable_chatgpt or not self.enable_chatgpt_assistant_response_channel:
            return users
        if not users and self.rule_ids.filtered(lambda r: r.chatbot_script_id and r.chatbot_script_id.chatgpt_asistant):
            users = self.env.ref('chatgpt_assistant_discuss_integration.user_chatgpt')
        return users
