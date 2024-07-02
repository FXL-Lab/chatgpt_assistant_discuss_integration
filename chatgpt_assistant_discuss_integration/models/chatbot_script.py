from odoo import models, fields


class ChatbotScript(models.Model):
    _inherit = 'chatbot.script'

    chatgpt_asistant = fields.Boolean(string="ChatGPT Asistant")
