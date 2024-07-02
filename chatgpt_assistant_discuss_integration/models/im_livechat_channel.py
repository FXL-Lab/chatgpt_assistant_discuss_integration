from odoo import api, Command, fields, models


class ImLivechatChannel(models.Model):
    _inherit = 'im_livechat.channel'

    def _get_available_users(self):
        users = super(ImLivechatChannel, self)._get_available_users()
        if not users and self.rule_ids.filtered(lambda p: p.chatbot_script_id and p.chatbot_script_id.chatgpt_asistant):
            users = self.env.ref('chatgpt_assistant_discuss_integration.user_chatgpt')
        return users
