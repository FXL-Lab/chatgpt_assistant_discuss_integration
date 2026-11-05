from odoo import api, fields, models


class WebsiteVisitor(models.Model):
    _inherit = 'website.visitor'

    chatgpt_contact_partner_id = fields.Many2one(
        'res.partner',
        string='Extracted Contact',
        copy=False,
        index='btree_not_null',
    )

    @api.depends('access_token', 'chatgpt_contact_partner_id')
    def _compute_partner_id(self):
        super()._compute_partner_id()
        for visitor in self:
            if not visitor.partner_id and visitor.chatgpt_contact_partner_id:
                visitor.partner_id = visitor.chatgpt_contact_partner_id
