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

    @api.depends('user_ids.im_status')
    def _compute_available_operator_ids(self):
        param_enable_chatgpt = self.env['ir.config_parameter'].sudo().get_param(
            'chatgpt_assistant_discuss_integration.enable_chatgpt_assistant_response'
        )
        for record in self:
            available_users = record.user_ids.filtered(lambda u: u._is_user_available())
            if not available_users and param_enable_chatgpt and record.enable_chatgpt_assistant_response_channel:
                if record.rule_ids.filtered(lambda r: r.chatbot_script_id and r.chatbot_script_id.chatgpt_asistant):
                    available_users = self.env.ref('chatgpt_assistant_discuss_integration.user_chatgpt')
            record.available_operator_ids = available_users
