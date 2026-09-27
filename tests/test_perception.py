from jevii_android.models import TargetStrategy
from jevii_android.perception import HierarchyParser

XML = '''<hierarchy rotation="0">
<node index="0" text="" resource-id="" class="android.widget.FrameLayout" clickable="false" enabled="true" bounds="[0,0][1080,2400]">
  <node index="0" text="Settings" resource-id="com.example:id/settings_card" class="android.view.ViewGroup" clickable="false" enabled="true" bounds="[0,1200][1080,1900]">
    <node index="0" text="Network &amp; internet" resource-id="com.example:id/network" class="android.widget.TextView" clickable="false" enabled="true" bounds="[50,1300][400,1400]" />
    <node index="1" text="Open" resource-id="com.example:id/open_settings" content-desc="Open settings" class="android.widget.Button" clickable="true" enabled="true" bounds="[80,1600][1000,1780]" />
  </node>
</node>
</hierarchy>'''


def test_parser_builds_rich_candidate():
    elements, quality = HierarchyParser().parse(XML)
    assert len(elements) == 1
    e = elements[0]
    assert e.text == "Open"
    assert e.resource_id == "com.example:id/open_settings"
    assert "Network & internet" in e.nearby_text
    assert e.locators[0].strategy == TargetStrategy.RESOURCE_ID
    assert e.locators[0].reliability > .9
    assert quality.score > 0


def test_fingerprint_changes_with_ui():
    p = HierarchyParser()
    a, _ = p.parse(XML)
    b, _ = p.parse(XML.replace("Open settings", "Settings opened"))
    assert p.fingerprint("pkg", "A", a) != p.fingerprint("pkg", "A", b)
