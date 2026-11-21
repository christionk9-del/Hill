import json
import os


def render(report: dict) -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "dashboard_template.html")) as f:
        tpl = f.read()
    data = json.dumps(report, default=str).replace("</", "<\\/")
    return tpl.replace("/*__EDGELAB_DATA__*/null", data)
