"""Rounded surfaces for the native ttk widgets."""


def configure_surfaces(app, style, *, surface, elevated, border, accent, text, muted):
    buttons = (
        ('TButton', 'Surface.button', elevated, border, text),
        ('Primary.TButton', 'Surface.primary', accent, None, '#ffffff'),
        ('Segment.TButton', 'Surface.segment', surface, border, muted),
        ('Selected.Segment.TButton', 'Surface.selectedSegment', accent, '#4b8cff', '#ffffff'),
    )
    for button_style, _, fill, edge, foreground in buttons:
        style.configure(button_style, background=fill, bordercolor=edge or fill,
                        foreground=foreground, relief='flat', width=0)
        style.map(button_style, foreground=[('disabled', '#8290a5'), ('!disabled', foreground)])
    style.configure('Badge.TLabel', foreground='#b9d4ff', padding=(9, 3))
    for name, foreground, fill in (
        ('Success.Badge.TLabel', '#c4f7ce', '#153a25'),
        ('Warning.Badge.TLabel', '#ffdc92', '#3b2c15'),
        ('Connected.Badge.TLabel', '#80ed91', '#153a25'),
        ('Neutral.Badge.TLabel', muted, elevated),
    ):
        style.configure(name, foreground=foreground, background=fill, padding=(9, 3))
    for name, edge in (('Capture.Card.TFrame', border), ('Selected.Card.TFrame', accent)):
        style.configure(name, background=surface, bordercolor=edge, borderwidth=1, relief='solid')
    style.configure('Preview.TLabel', foreground=muted)
    try:
        from PIL import Image, ImageColor, ImageDraw, ImageTk
    except ImportError:
        return  # Match the image preview's optional Pillow dependency.

    images = []

    def blend(start, end, amount):
        return tuple(
            round(a + (b - a) * amount)
            for a, b in zip(ImageColor.getrgb(start), ImageColor.getrgb(end))
        )

    def rounded(fill, edge, radius, center):
        # Paint at 3x before shrinking to antialias the transparent corners.
        scale = 3
        width, height = ((2 * (radius + 1) + side) * scale for side in center)
        image = Image.new('RGBA', (width, height))
        ImageDraw.Draw(image).rounded_rectangle(
            (0, 0, width - 1, height - 1), radius * scale, fill=fill,
            outline=edge, width=scale if edge else 0,
        )
        photo = ImageTk.PhotoImage(
            image.resize((width // scale, height // scale), Image.Resampling.LANCZOS),
            master=app,
        )
        images.append(photo)
        return photo

    def element(name, fill, edge, radius, center, states=(), min_height=0):
        normal = rounded(fill, edge, radius, center)
        variants = [
            (*state, rounded(state_fill, state_edge, radius, center))
            for state, state_fill, state_edge in states
        ]
        # Tk tiles the center and edges; large tiles avoid thousands of draws on macOS.
        style.element_create(
            name, 'image', normal, *variants,
            border=radius + 1, padding=0, width=0, height=min_height, sticky='nsew',
        )

    def label_layout(name, element_name):
        style.layout(name, [(element_name, {'sticky': 'nsew', 'children': [
            ('Label.padding', {'sticky': 'nsew', 'children': [
                ('Label.label', {'sticky': 'nsew'}),
            ]}),
        ]})])

    element('Surface.card', surface, border, 8, (256, 256))
    style.layout('Card.TFrame', [('Surface.card', {'sticky': 'nsew'})])
    style.configure('Card.TFrame', background=app.cget('background'))
    for frame_style, name, edge in (
        ('Capture.Card.TFrame', 'Surface.capture', border),
        ('Selected.Card.TFrame', 'Surface.selectedCard', '#3c83ff'),
    ):
        element(name, surface, edge, 8, (256, 256))
        style.layout(frame_style, [(name, {'sticky': 'nsew'})])
        style.configure(frame_style, background=surface)
    element('Surface.preview', blend(surface, '#000000', 0.19), None, 9, (256, 256))
    label_layout('Preview.TLabel', 'Surface.preview')
    style.configure('Preview.TLabel', background=surface)
    element('Surface.badge', blend(surface, accent, 0.18), None, 9, (192, 24))
    label_layout('Badge.TLabel', 'Surface.badge')
    style.configure('Badge.TLabel', background=app.cget('background'))
    for badge_style, name, fill, edge, backing in (
        ('Success.Badge.TLabel', 'Surface.success', '#153a25', '#245738', app.cget('background')),
        ('Warning.Badge.TLabel', 'Surface.warning', '#3b2c15', '#846529', app.cget('background')),
        ('Connected.Badge.TLabel', 'Surface.connected', '#153a25', '#245738', surface),
        ('Neutral.Badge.TLabel', 'Surface.neutral', elevated, border, surface),
    ):
        element(name, fill, edge, 8, (192, 24))
        label_layout(badge_style, name)
        style.configure(badge_style, background=backing)

    focus_edge = blend(accent, '#ffffff', 0.54)
    disabled_fill = blend(surface, elevated, 0.5)
    for button_style, name, fill, edge, _ in buttons:
        pressed = blend(fill, '#000000', 0.18)
        active = blend(fill, '#ffffff', 0.05)
        element(name, fill, edge, 8, (192, 24), states=(
            (('disabled',), disabled_fill, None),
            (('pressed', 'focus'), pressed, focus_edge),
            (('active', 'focus'), active, focus_edge),
            (('focus',), fill, focus_edge),
            (('pressed',), pressed, edge),
            (('active',), active, edge),
        ), min_height=18)
        style.layout(button_style, [(name, {'sticky': 'nsew', 'children': [
            ('Button.padding', {'sticky': 'nsew', 'children': [
                ('Button.label', {'sticky': 'nsew'}),
            ]}),
        ]})])
        # This color shows through the skin at the corners, including when disabled.
        style.configure(button_style, background=surface)
        style.map(button_style, background=[('disabled', surface), ('!disabled', surface)])
    app._surface_images = images

    def toggle(selected, disabled=False):
        scale = 3
        width, height = 32, 18
        image = Image.new('RGBA', (width * scale, height * scale))
        draw = ImageDraw.Draw(image)
        fill = accent if selected else '#3c4754'
        if disabled:
            fill = '#303b48'
        draw.rounded_rectangle((0, 0, width * scale - 1, height * scale - 1),
                               radius=9 * scale, fill=fill)
        left = 16 if selected else 2
        draw.ellipse((left * scale, 2 * scale, (left + 14) * scale, 16 * scale),
                     fill='#94a0af' if disabled else '#ffffff')
        photo = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS), master=app)
        images.append(photo)
        return photo

    toggle_images = [toggle(False), toggle(True), toggle(False, True), toggle(True, True)]
    style.element_create('Surface.toggle', 'image', toggle_images[0],
                         ('disabled', 'selected', toggle_images[3]),
                         ('disabled', toggle_images[2]), ('selected', toggle_images[1]),
                         padding=(0, 0, 7, 0), sticky='w')
    style.layout('Toggle.TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
        ('Surface.toggle', {'side': 'left', 'sticky': ''}),
        ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
            ('Checkbutton.label', {'sticky': 'nswe'}),
        ]}),
    ]})])
