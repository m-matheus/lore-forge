// Midnight Realm: builds the editable thumbnail template (PSD) from the band layers.
//
// Run in Photoshop: File > Scripts > Browse... > this file (it must sit next to
// fade.png, rule.png, plaque.png, seal.png; make_thumb_band.py copies it there).
// Needs the Cinzel font installed in Windows (channels/midnight-realm/assets/fonts/
// title.ttf > right click > Install), otherwise Photoshop substitutes it.
//
// Result: midnight_realm_thumb.psd with
//   KEY ART        drop the art here (sample_art.png if present)
//   BAND (group)   fade, rule, plaque, seal, and live text: length, title, channel
// The positions mirror the constants at the top of make_thumb_band.py.
#target photoshop

(function () {
    var here = File($.fileName).parent;
    var W = 1280, H = 720;
    var CY = 678;                                     // band content centre line

    var oldRuler = app.preferences.rulerUnits, oldType = app.preferences.typeUnits;
    app.preferences.rulerUnits = Units.PIXELS;
    app.preferences.typeUnits = TypeUnits.PIXELS;

    try {
        var black = findCinzel("Black"), bold = findCinzel("Bold");
        if (!black) {
            alert("Cinzel is not installed, so Photoshop will substitute the text font.\n\n" +
                  "Install channels/midnight-realm/assets/fonts/title.ttf (right click > Install), " +
                  "restart Photoshop and run this script again for the real look.");
        }

        var doc = app.documents.add(W, H, 72, "midnight_realm_thumb", NewDocumentMode.RGB,
                                    DocumentFill.TRANSPARENT);

        // Key art at the bottom.
        var art = new File(here + "/sample_art.png");
        var artLayer;
        if (art.exists) {
            artLayer = placeLayer(doc, art);
        } else {
            artLayer = doc.artLayers.add();
            doc.selection.selectAll();
            doc.selection.fill(color("1B1E24"));
            doc.selection.deselect();
        }
        artLayer.name = "KEY ART (replace)";

        // The band, in its own group so it can be dragged onto any art.
        var band = doc.layerSets.add();
        band.name = "BAND";
        var names = ["fade", "rule", "plaque", "seal"];
        for (var i = 0; i < names.length; i++) {
            var l = placeLayer(doc, new File(here + "/" + names[i] + ".png"));
            l.name = names[i];
            l.move(band, ElementPlacement.PLACEATBEGINNING);
        }

        //      group name       text               x    y        size font   colour    tracking align
        addText(band, "length",  "2+ HOURS",        237, CY + 1,  24, black, "07080A", 120, Justification.CENTER);
        addText(band, "title",   "BLOODBORNE LORE", 640, CY,      42, black, "EDE6D6", 60,  Justification.CENTER);
        addText(band, "channel 1", "MIDNIGHT",      1012, CY - 10, 16, bold, "EDE6D6", 180, Justification.LEFT);
        addText(band, "channel 2", "REALM",         1012, CY + 11, 16, bold, "E0AA55", 180, Justification.LEFT);

        doc.saveAs(new File(here + "/midnight_realm_thumb.psd"), new PhotoshopSaveOptions(), false);
    } finally {
        app.preferences.rulerUnits = oldRuler;
        app.preferences.typeUnits = oldType;
    }

    // Copy a full-canvas PNG into doc as a layer; same size, so it lands in place.
    function placeLayer(target, file) {
        var src = app.open(file);
        var dup = src.layers[0].duplicate(target, ElementPlacement.PLACEATBEGINNING);
        src.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument = target;
        return dup;
    }

    // Point text vertically centred on y: the cap-height centre sits ~0.36em above the baseline.
    function addText(group, name, contents, x, y, size, fontName, hex, tracking, justification) {
        var layer = group.artLayers.add();
        layer.kind = LayerKind.TEXT;
        layer.name = name;
        var t = layer.textItem;
        t.contents = contents;
        if (fontName) t.font = fontName;
        t.size = size;
        t.tracking = tracking;
        t.justification = justification;
        t.color = color(hex);
        t.position = [x, y + size * 0.36];
        return layer;
    }

    function color(hex) {
        var c = new SolidColor();
        c.rgb.hexValue = hex;
        return c;
    }

    // Cinzel is a variable font: its named instances show up as styles Regular/Bold/Black.
    function findCinzel(style) {
        var fallback = null;
        for (var i = 0; i < app.fonts.length; i++) {
            var f = app.fonts[i];
            if (f.family !== "Cinzel") continue;
            if (f.style === style) return f.postScriptName;
            fallback = fallback || f.postScriptName;
        }
        return fallback;
    }
})();
