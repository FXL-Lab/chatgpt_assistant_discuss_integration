# -*- coding: utf-8 -*-
# Copyright (c) 2024-Present Elias Owis. (<https://engelias.website//>)

{
    'name': "chatgpt_assistant_discuss_integration",

    'summary': "ChatGPT Assistant Integration with Discuss App",

    'description': """
        ChatGPT Assistant Integration with Discuss App
    """,

    'author': "Elias Owis",
    'website': "https://engelias.website",
    'category': 'Uncategorized',
    'version': '0.1',

    'depends': ['base', 'base_setup', 'mail', 'im_livechat'],
    'external_dependencies': {'python': ['openai']},

    'license': 'LGPL-3',
    'maintainer': 'Elias Owis',

    'data': [
        'security/ir.model.access.csv',
        'data/mail_channel_data.xml',
        'data/user_partner_data.xml',
        'views/res_config_settings_views.xml',
    ],
    'images': [
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
