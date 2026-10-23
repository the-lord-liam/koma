# Koma

> **Work in progress.** Koma is not a finished product. Things may not work properly, features can change or break, and drawing and story planning have not been tested end to end with a real xAI key yet. Use it at your own risk.

Koma is also a concept: an idea of what a free, local manga and illustration studio could be.

Contact the creator <a href="https://x.com/the_lord_liam"><img src="docs/x-icon.png" width="22" height="22" alt="X"></a>

Koma is a local manga studio for Ubuntu. It keeps the book, the drawings, and the API key on your machine. Home shows two canvases: a manga page, and a separate illustration. The manga side plans a series, keeps character designs locked to reference art, draws one panel at a time, letters the page in the app, and exports chapters. The illustration side is one picture, with no panels.

## Privacy

Koma has no telemetry, analytics, or tracking, and collects no data. Your books, drawings, 3D models, saved poses, and page undo stay in `~/.local/share/koma`. Settings and your API key stay in `~/.config/koma/config.json` (or in `XAI_API_KEY` if that is already set and the file has no key). Exported pages are written to `~/Documents/Koma/`. The only network traffic is when you use the AI features. Story planning and the bot send that text to xAI (`api.x.ai`) with your own key, and those requests ask xAI not to store them. Drawing sends the prompt and any reference images to the same API, and the picture comes back in the reply. If xAI returns a link to the picture instead, Koma downloads that link and nothing else. xAI has no 3D API. A 3D model built with the local backend stays on your machine. That path only contacts xAI when a reference picture has to be drawn first. The first time TripoSR runs, that program downloads its own weights from Hugging Face (`huggingface.co`, model `stabilityai/TripoSR`). It does not upload the character. If you choose Meshy in Settings, the prompt and any reference image go to `api.meshy.ai`, and Koma downloads the model from the HTTPS link Meshy returns. If you choose Tripo, the same happens with `api.tripo3d.ai`. Those services are used only when you select them and generate a model. The X button on the About page only takes you to the creator's X profile.

## What it looks like

Home is two canvases, with a search field above them. Manga opens the book. Illustration is one picture.

<p align="center">
  <img src="docs/home.png" width="900" alt="Home, with a manga canvas and an illustration canvas">
</p>

A new manga asks for the title, Japanese or Western, a page size, and the print resolution.

<p align="center">
  <img src="docs/setup.png" width="900" alt="Setup for Harbor, Japanese manga, B6, print at 300 dpi">
</p>

The page desk keeps the book on the left, the page in the middle, and the Imagine tools on the right.

<p align="center">
  <img src="docs/page.png" width="900" alt="Page view with panel 1 selected and the Imagine properties open">
</p>

Story uses the same chain: title, volume, chapter, and page. The properties for the selected row sit on the right.

<p align="center">
  <img src="docs/story.png" width="900" alt="Story view for Harbor, with the title selected">
</p>

About says who the studio is for. The age rating is Everyone.

<p align="center">
  <img src="docs/about.png" width="900" alt="About page: See an idea become pages, age rating Everyone">
</p>

## Open it

The first launch adds **Koma** to the app grid.

```bash
~/koma/bin/koma
```

## What you do

Koma opens on the two canvases. **Canvases** in the header comes back here. Tap **Manga** and it asks for the title, whether the book is Japanese manga or Western comics, a page size, and the resolution. Then the page opens. Tap **Illustration** to draw a single picture. The **+** beside each name starts another canvas. The menu under a manga title switches books when you have more than one. **Delete** on the home canvas, or **Delete manga** in Story, removes that book, its rows, and its image files.

Search on the home screen looks through manga titles, chapter titles, dialogue, notes, the cast, and names on the reference board. A title or chapter hit opens Story. A dialogue hit opens that page and panel. A note opens that note. A cast hit opens the character. A reference hit opens the board.

1. **Story.** Write a premise and plan the chapters, or type the outline yourself. A plan stays as a draft until you apply it. Switch volumes from the volume menu. **Duplicate chapter** copies the chapter and every page in it, including the drawings, and places the copy just after the original. **References** is a board for pictures already on your machine: poses, places, colors, anything you want nearby while you write. Koma copies the file into the book. It is not uploaded, and it is not sent when a panel is drawn. Press **R** to open the board. **?** lists the other shortcuts.
2. **Cast.** Describe each person, then draw a portrait. Later panels use that portrait as the identity reference. Turnaround, expression, and pose sheets are optional. **Import portrait** uses an image already on your machine. The first imported portrait becomes the identity later panels follow. **3D model** builds a mesh for that character. **From prompt** uses the text (and, on the local backend, a picture). **From references** lifts the portrait or turnaround you already have. **Import GLB, VRM, or OBJ** opens a model already on your machine and keeps it with that character. A VRM file is read as a GLB. Textures inside the model are shown, and a texture that sits next to the file is copied in with it. The cast list shows a small picture of the model once the viewer has drawn it. Drag the model to orbit it in any direction. Shift-drag pans, Alt-drag rolls, and the scroll wheel zooms. **Front**, **Side**, and **Three-quarter** turn the camera. Expression buttons preview morph targets on a rigged file such as a VRM. **Pose** selects a bone from the list or from the model and rotates it with the handles or the sliders. **Mirror** copies the left arm and leg onto the right. **T / A pose** restores the file's bind pose. Standing, walking, running, a running mid-stride, sitting, kneeling, crouching, pointing, a wave, arms crossed, and a fighting stance are built in. A VRM uses its humanoid bone map; other rigs are matched by bone name. **Save pose** keeps that stance, and the current expression, on the character. **Reset view**, **Auto-rotate**, **Wireframe**, **Lighting**, **Grid**, **Light ground**, and **Large view** sit under the model. **Save this angle** stores the current view, including the pose, as a pose reference on that character. **Use on panel** does the same and attaches the picture to the panel you have open. **Play** runs a clip stored in the model. Pause, or drag the timeline, and the saved angle uses that frame. **Import animation** reads another GLB or VRM from your machine when the bone names or the humanoid map match. **Export GLB** and **Export OBJ** write the original model under `~/Documents/Koma/<title>/`. **Export posed GLB** writes a separate file with the pose, the expression, and the paused frame baked in.
3. **Places.** Draw an establishing view for a location and attach it to panels. **Import picture** uses a file already on your machine.
4. **Pages.** Pick a layout. The left rail shows a thumbnail of each page. Set the shot, camera, expression, composition, and action, then draw the panel. Keys **1** through **6** select the page tools. **Draw empty panels** fills every blank panel on the page. **Match the previous panel** sends the last drawing before this one along with the request, so a scene keeps its costumes and place. Revise a drawing, or go back to an earlier version. **Earlier** and **Later** move a panel. Drag a panel on the sheet, or a page in the list, to reorder it. **Delete panel** removes one frame, and the remaining frames reflow to fill the page. A page always keeps at least one panel. Choosing a smaller layout asks before it drops panels that already have art or lettering. **Duplicate page** copies the page, including its drawings and reference images, as a separate page you can restage. **Print preview** shows the lettered page the export will use. **Ctrl+Z** undoes a page-desk edit and **Ctrl+Shift+Z** redoes it. The last 20 edits on each page are stored on disk, so they are still there after you quit. The header shows **Saving** while a change is written and **Saved** when it lands. Press **?** for the shortcut list.
5. **Read.** The chapter is assembled as finished pages, in order. Earlier and Later, or the left and right arrow keys, turn the page. Click a page to edit it. Export a chapter as PDF or CBZ from the page menu or from here. The PDF page is the book's finish size. Files land in `~/Documents/Koma/<title>/`. Titles in any language get their own folder.

Dialogue and sound effects are drawn by the app, not by the image model, so the words stay the ones you typed. A long sound effect shrinks to fit the panel instead of being cut off. Japanese and other CJK lettering uses a CJK font. Latin text uses that face when it includes Latin letters, and a Latin face otherwise. Balloon presets set a speech balloon, a shout, a thought, or a caption in one click.

An empty manga, chapter, or character name is refused with a short message. A missing or unreadable panel image is drawn as a plain placeholder, so export still finishes. If the export folder cannot be written, if the library is open in another window, or if the library file is damaged, Koma says so instead of failing quietly. A page image that cannot be read is reported on its own, rather than as a read-only folder. A damaged library stops at startup and asks you to move `koma.sqlite` aside.

## API key

Image and story generation use your xAI account (`grok-4.7` for the outline, `grok-imagine-image-2.0` for art).

In Settings, paste a key from [console.x.ai](https://console.x.ai). It is stored in `~/.config/koma/config.json` with permissions `600`. If `XAI_API_KEY` is already set in the environment, Koma uses that until you save a different key. With no key, drawing and planning say to add one. If the API cannot be reached, the message says so and does not include the key.

Resolution defaults to **1k**. Switch to **2k** in Settings for finished pages. Image quality is **low**, **medium**, or **auto**, and that choice is sent with the image request.

You can build the whole outline, letter the pages, and export with no key. Drawing needs one.

## 3D models

Each character can have a 3D model as well as a portrait. Generate it from a prompt, or from the portrait and sheets already on that character, or import a `.glb`, `.vrm`, or `.obj` file from your machine. A `.vrm` file is glTF, so Koma stores it with the other models. Importing reads that file and stores a copy under `meshes/`. Textures packed in the file are kept, including MToon and unlit materials. A texture that sits beside the file (a GLB image path, or an OBJ `.mtl` `map_Kd`) is copied into that copy. It does not upload the model.

### Viewer

The viewer ships with Koma. It does not load three.js, `<model-viewer>`, or anything else from a CDN, so it works with no network. It draws the model's base color, so a toon texture shows up instead of a flat stand-in. Separate parts are placed where the file puts them. The camera fits the model's height, width, and depth, so a tall character or a long animal stays in frame. Drag orbits freely, including upside down. Shift-drag pans, Alt-drag rolls, and scroll zooms. Reset view puts the camera back. Auto-rotate spins the model until you turn it off. Wireframe and Lighting can be on together or one at a time. Grid draws a floor under the figure. Light ground switches the backdrop. Large view fills the window; Close, or Escape, leaves it. On a desktop that allows it, Large view also asks the window to go fullscreen.

Sendagaya Shino (CC0) in the Standing pose, arms down, from a three-quarter front. Grid and lighting are on, and the camera and display controls sit beside the model. The pose handles are hidden.

<p align="center">
  <img src="docs/3d-viewer.png" width="900" alt="3D viewer on Sendagaya Shino standing with arms down, three-quarter front, grid and lighting on, pose handles hidden">
</p>

### Textures

The viewer draws the texture packed in the file, so a toon model keeps its painted face and clothes.

The same model, head and shoulders. The painted face and the sailor uniform are the texture from the file.

<p align="center">
  <img src="docs/3d-texture.png" width="360" alt="Head and shoulders of Sendagaya Shino, painted face and sailor uniform">
</p>

### Expressions

Expression buttons add the model's morph targets (a VRM blink or a vowel shape). An expression can stay on while the pose changes.

Rest, Joy, and Surprised, still head and shoulders. Joy narrows the eyes and lifts the mouth. Surprised opens the eyes and the mouth.

<p align="center">
  <img src="docs/3d-expression.png" width="900" alt="Sendagaya Shino, head and shoulders: Rest, Joy, and Surprised">
</p>

### Posing

Pose mode bends a rigged GLB or VRM. Pick a bone in the list, grouped by body part, or click it on the model, then drag the red, green, and blue handles or move the X, Y, and Z sliders. Show bones turns those markers and handles off when you want a clean view. It starts on. T / A pose puts back the bind pose stored in the file, which is a T-pose or an A-pose depending on how the model was rigged. Standing, walking, running, a running mid-stride, sitting, kneeling, crouching, pointing, a wave, arms crossed, and fighting stance aim the limbs. Wave keeps the head level and the raised hand beside it. Kneeling, crouching, and the mid-stride plant the lower foot on the floor. VRM files use the humanoid bone map. A rig without that map is matched from bone names such as `LeftUpperArm` or `mixamorig:LeftArm`. Save pose stores the bone rotations and the current expression in the library under that character. Load puts them back. Nothing about posing is uploaded.

Four poses on Sendagaya Shino, from a three-quarter front, with the floor and lighting on. Standing hangs the arms. Sitting puts her on a box, knees folded forward, hands in the lap, feet at the floor. Fighting holds both fists in front of the chest, elbows bent, one foot forward, face clear. Pointing holds the right arm forward at shoulder height with a straight elbow, the head level, the hand clear of the face, and the other arm down.

<p align="center">
  <img src="docs/3d-pose-grid.png" width="900" alt="Two by two grid of Sendagaya Shino standing, sitting on a box, fighting, and pointing, each pose labeled">
</p>

### Animation

A model that already has animation clips lists them under the viewer. Play and pause, a loop switch, and 0.5×, 1×, and 2× set the speed. The timeline stops on any frame. Save this angle and Use on panel capture that frame, expression included. Import animation opens a GLB or VRM from your machine and plays it on this rig when the bone names match, or when both files share a humanoid bone map. The imported clip is kept for this visit. It is not written back into the model and it is not uploaded. Root travel on a walk is held in place so the figure stays in frame; the legs still move.

Clip 1 paused at 0.80 seconds, one step into the walk, from a three-quarter front. CesiumMan by Cesium, licensed CC-BY 4.0.

<p align="center">
  <img src="docs/3d-animation.png" width="900" alt="CesiumMan walk paused at 0.80 seconds, three-quarter front, timeline open">
</p>

Save this angle writes a PNG of the posed model, expression included, next to the character's other pictures. It does not upload it. Use on panel copies that picture onto the open panel as a reference, the same way an imported panel reference is used when the panel is drawn. The cast thumbnail is a small copy of the view and is removed with the model. Named poses are rows in the library database, deleted with the character or the project, and they are not sent anywhere. Export GLB and Export OBJ write the model you imported or generated. Export posed GLB writes a new file beside those, with the current pose, expression, and animation frame baked into the vertices. The stored model is left as it was.

The default provider is **local TripoSR**. xAI's API covers text, images, video, and voice, and it has no 3D endpoint, so Koma does not pretend to call one. TripoSR is MIT-licensed, it is the lightest of the open image-to-3D tools that fit this app, and it does not need a credit card. It turns one picture into a mesh. A prompt with no portrait is drawn first with xAI, then lifted. A GPU with about 6 GB free is comfortable. CPU works and is slow, often several minutes for one character. CUDA builds of PyTorch have to match the driver; a CPU build is enough to start.

Koma does not ship the weights. Install TripoSR beside the library and install its Python packages:

```bash
git clone https://github.com/VAST-AI-Research/TripoSR.git ~/.local/share/koma/backends/triposr
python3 -m pip install --user torch --index-url https://download.pytorch.org/whl/cpu
python3 -m pip install --user -r ~/.local/share/koma/backends/triposr/requirements.txt
```

Koma finds that folder and runs `extras/mesh/triposr_glb.py`. Until it is installed, Generate says the backend is not installed and how to set it up. Another image-to-3D tool can be used by setting **Local 3D command** in Settings (or `KOMA_MESH_CMD`) to a program that accepts `image.png --output model.glb`. An OBJ written to that path is turned into a GLB. Hunyuan3D and TRELLIS need much more GPU memory and have narrower licenses, so they are not the default. Stable Fast 3D is a similar size to TripoSR, and its license limits some commercial use, so it stays optional behind that command.

**Meshy** and **Tripo** are optional hosted providers. Paste your own key in Settings. It is stored in the same private config file. `MESHY_API_KEY` and `TRIPO_API_KEY` are used when the file has no key for that service. Hosted text-to-3D sends the prompt. Hosted image-to-3D sends one reference picture. Both download a GLB over HTTPS. They bill the account on that key, so they are not the default. A missing key is reported in the studio and the key is not repeated in the error.

## Where files live

| What | Where |
| --- | --- |
| Project database | `~/.local/share/koma/koma.sqlite` |
| Generated images | `~/.local/share/koma/images/` |
| 3D models | `~/.local/share/koma/meshes/` |
| Page undo | `~/.local/share/koma/history/` |
| Exports | `~/Documents/Koma/<title>/` |

Deleting a character removes that character's portraits, 3D models, and saved poses. Deleting a project removes its database rows, image files, 3D models, and saved poses.

## Get the source

```bash
git clone https://github.com/the-lord-liam/koma.git
```

The repository is public. Use Fork on GitHub to start your own copy.

## Make it your own

You're welcome to fork Koma, but you don't have to stop there. Feel free to take the idea and the concept and run with them in your own projects, whether that's building your own manga studio from scratch or remixing pieces of this one. No need to ask. If Koma sparks something, I'd love to see what you make.

## License

Koma is free and open source under the [MIT license](LICENSE). Fork it, change it, and share your version. The copyright notice in that file needs to stay with the copies you share.

An xAI API key stays on your machine, in `~/.config/koma/config.json` or in `XAI_API_KEY`. It is not part of this repository.
