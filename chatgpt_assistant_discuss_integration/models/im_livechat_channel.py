import logging
from odoo import api, Command, fields, models
from .models import DEFAULT_CHATGPT_INACTIVITY_SYSTEM_PROMPT, _get_openai_model_options

_logger = logging.getLogger(__name__)


class ImLivechatChannel(models.Model):
    _inherit = 'im_livechat.channel'

    enable_chatgpt_assistant_response_channel = fields.Boolean(
        string="Enable ChatGPT Assistant in this channel",
        default=False,
    )
    prompt_id = fields.Char(
        string="Prompt ID",
        help="ID of the OpenAI dashboard prompt to use in this channel.",
        default=''
    )
    chatgpt_model = fields.Selection(
        selection=lambda self: _get_openai_model_options(
            self.env, self.mapped('chatgpt_model')
        ),
        string="OpenAI Model",
        help="Optional model override for ChatGPT responses in this channel.",
    )
    chatgpt_instructions = fields.Text(
        string="ChatGPT Instructions",
        help="Instructions for ChatGPT in this channel. Uses the default instructions when empty.",
    )
    chatgpt_vector_store_id = fields.Char(
        string="OpenAI Vector Store ID",
        help="Optional vector store for file search in this channel. Uses the default when empty.",
    )
    chatgpt_inactivity_timeout = fields.Integer(
        string="Follow-up After (Minutes)",
        help="Send one follow-up after this many minutes without a new message. Set to 0 to disable.",
        default=0,
    )
    chatgpt_inactivity_system_prompt = fields.Text(
        string="Inactivity System Prompt",
        default=DEFAULT_CHATGPT_INACTIVITY_SYSTEM_PROMPT,
        help="System instructions used to generate this channel's inactivity follow-up message.",
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
