# Green-eyed catgirl skin illustration

`green-eyed-catgirl.png` is a newly generated, reference-guided character illustration for 拾歌 · VTuber SongCut. The two appearance references were screenshots supplied by the user; they are used only as private visual references and are not copied into this repository.

- Method: Codex built-in `image_gen` tool, with both user screenshots passed as `referenced_image_paths` and `transparent_background: true`. No CLI/API fallback, manual tracing, or SVG substitute.
- SHA-256: `cf9f4af2668bcc74936b6cb592688adc1a96042b6f82162fc35269835a64ec31`.
- Project asset: `apps/web/src/assets/skins/green-eyed-catgirl.png`.
- Requested canvas: 1024 × 1024. Actual native output: 1254 × 1254 PNG with alpha; preserved at original size without image editing or resampling.
- Validation: visually checked the complete ears, shoulders, hands, plain book, green eyes, bow colors, modest clothing, and absence of text, UI, screenshot background, watermarks, or extra characters. PNG alpha channel verified.

## Generation prompt

```text
Use case: stylized-concept
Asset type: finished 1024x1024 PNG transparent character illustration for the music clipping app 拾歌 · VTuber SongCut, displayed on a theme page and beside empty states, legible when reduced to 240 pixels.
Input images: Image 1 and Image 2 are user-provided character appearance and anime illustration style references only. Re-draw the referenced character in a fresh pose; do not reproduce either screenshot or its surroundings.
Scene/backdrop: genuinely transparent alpha background; isolated character, no scenery or solid backdrop.
Subject: a sweet black-haired catgirl based closely on the reference. Short-to-medium black hair with a straight rounded fringe and small side-tied tufts, black cat ears with warm cream inner ears, very clear large emerald green / teal-green eyes, small purple bow at viewer left and dark red bow at viewer right. Soft natural closed-mouth smile with a calm, welcoming expression. Modest gray-white-black sailor-collar blouse, neat white shoulder ruffles and fine pale piping; reference-inspired subtle violet highlights in the black hair.
Pose and prop: upright relaxed front-facing upper body with a very slight head tilt, both hands gently holding one small plain muted lavender songbook at mid-torso. The songbook cover has no text, symbols or branding. This is the only accessory/prop.
Style/medium: polished high quality soft anime cel-shaded illustration, delicate clean outlines, restrained gentle shading, beautiful eyes, clean readable silhouette and polished edges. Preserve the character identity from the two reference screenshots.
Composition/framing: square 1024x1024 canvas, centered upper-body character from full ears down to waist, with both ears, all hair, shoulders, hands and book completely inside the frame. Approximately 10 percent transparent breathing room on all four sides. Face in upper half; blouse lower edge naturally curves and softly tapers to make a self-contained cutout.
Lighting/mood: soft neutral light, affectionate and serene; appropriate as a friendly music editing app skin.
Constraints: entirely modest, nonsexual; exactly one character; actual transparency; clean antialiased alpha edges.
Avoid: any text, letters, captions, usernames, hair clips with lettering, UI, game background, small extra characters, microphone, headset, watermark, brand logos, signature, framed card, fake checkerboard background, dramatic effects, excessive decorative objects, cropped ears or cropped shoulders.
```
