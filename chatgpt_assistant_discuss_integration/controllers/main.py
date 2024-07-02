from odoo import http, _
from odoo.http import request

from odoo.addons.im_livechat.controllers.main import LivechatController


class LivechatControllerEx(LivechatController):

    # Overide this method to show the chatbot if operator not available or not
    @http.route('/im_livechat/init', type='json', auth="public", cors="*")
    def livechat_init(self, channel_id):
        operator_available = len(request.env['im_livechat.channel'].sudo().browse(channel_id)._get_available_users())
        rule = {}
        # find the country from the request
        country_id = False
        country_code = request.geoip.get('country_code')
        if country_code:
            country_id = request.env['res.country'].sudo().search([('code', '=', country_code)], limit=1).id
        # extract url
        url = request.httprequest.headers.get('Referer')
        # find the first matching rule for the given country and url
        matching_rule = request.env['im_livechat.channel.rule'].sudo().match_rule(channel_id, url, country_id)
        if matching_rule and (not matching_rule.chatbot_script_id or matching_rule.chatbot_script_id.script_step_ids):
            frontend_lang = request.httprequest.cookies.get('frontend_lang', request.env.user.lang or 'en_US')
            matching_rule = matching_rule.with_context(lang=frontend_lang)
            rule = {
                'action': matching_rule.action,
                'auto_popup_timer': matching_rule.auto_popup_timer,
                'regex_url': matching_rule.regex_url,
            }
            if matching_rule.chatbot_script_id.active and (not matching_rule.chatbot_only_if_no_operator or
                                                           (not operator_available and matching_rule.chatbot_only_if_no_operator)) and matching_rule.chatbot_script_id.script_step_ids:
                chatbot_script = matching_rule.chatbot_script_id
                rule.update({'chatbot': chatbot_script._format_for_frontend()})
        return {
            'available_for_me': (rule and rule.get('chatbot'))
                                or operator_available and (not rule or rule['action'] != 'hide_button') or (matching_rule and matching_rule.chatbot_script_id.chatgpt_asistant),
            'rule': matching_rule,
        }
