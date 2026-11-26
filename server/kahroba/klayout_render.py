# KLayout batch script: render a GDS to PNG. Variables come from -rd.
import pya

view = pya.LayoutView()
view.set_config("background-color", "#0d1117")
view.set_config("grid-visible", "false")
view.set_config("text-visible", "false")
idx = view.load_layout(gds, 0)  # noqa: F821
if lyp:  # noqa: F821
    view.load_layer_props(lyp)  # noqa: F821
view.max_hier()
view.zoom_fit()
s = int(size)  # noqa: F821
view.save_image(out, s, s)  # noqa: F821
print("rendered", out)  # noqa: F821
