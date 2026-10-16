# Koma

> **Work in progress.** Koma is not a finished product. Things may not work properly, features can change or break, and drawing and story planning have not been tested end to end with a real xAI key yet. Use it at your own risk.

Koma is a local manga studio for Ubuntu. It keeps the book, the drawings, and the API key on your machine. Home shows two canvases: a manga page, and a separate illustration. The manga side plans a series, keeps character designs locked to reference art, draws one panel at a time, letters the page in the app, and exports chapters. The illustration side is one picture, with no panels.

## Privacy

Koma has no telemetry, analytics, or tracking, and collects no data. Your books, drawings, and page undo stay in `~/.local/share/koma`. Settings and your API key stay in `~/.config/koma/config.json` (or in `XAI_API_KEY` if that is already set and the file has no key). Exported pages are written to `~/Documents/Koma/`. The only network traffic is when you use the AI features. Story planning and the bot send that text to xAI (`api.x.ai`) with your own key, and those requests ask xAI not to store them. Drawing sends the prompt and any reference images to the same API, and the picture comes back in the reply. If xAI returns a link to the picture instead, Koma downloads that link and nothing else. Nothing is sent anywhere else. The X button on the About page only takes you to the creator's X profile.

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

Search on the home screen looks through manga titles, chapter titles, dialogue, notes, and the cast. A title or chapter hit opens Story. A dialogue hit opens that page and panel. A note opens that note. A cast hit opens the character.

1. **Story.** Write a premise and plan the chapters, or type the outline yourself. A plan stays as a draft until you apply it. Switch volumes from the volume menu. **Duplicate chapter** copies the chapter and every page in it, including the drawings, and places the copy just after the original.
2. **Cast.** Describe each person, then draw a portrait. Later panels use that portrait as the identity reference. Turnaround, expression, and pose sheets are optional. **Import portrait** uses an image already on your machine. The first imported portrait becomes the identity later panels follow.
3. **Places.** Draw an establishing view for a location and attach it to panels. **Import picture** uses a file already on your machine.
4. **Pages.** Pick a layout. The left rail shows a thumbnail of each page. Set the shot, camera, expression, composition, and action, then draw the panel. Keys **1** through **6** select the page tools. **Draw empty panels** fills every blank panel on the page. **Match the previous panel** sends the last drawing before this one along with the request, so a scene keeps its costumes and place. Revise a drawing, or go back to an earlier version. **Earlier** and **Later** move a panel. Drag a panel on the sheet, or a page in the list, to reorder it. **Delete panel** removes one frame, and the remaining frames reflow to fill the page. A page always keeps at least one panel. Choosing a smaller layout asks before it drops panels that already have art or lettering. **Duplicate page** copies the page, including its drawings and reference images, as a separate page you can restage. **Print preview** shows the lettered page the export will use. **Ctrl+Z** undoes a page-desk edit and **Ctrl+Shift+Z** redoes it. The last 20 edits on each page are stored on disk, so they are still there after you quit. The header shows **Saving** while a change is written and **Saved** when it lands. Press **?** for the shortcut list.
5. **Read.** The chapter is assembled as finished pages, in order. Earlier and Later, or the left and right arrow keys, turn the page. Click a page to edit it. Export a chapter as PDF or CBZ from the page menu or from here. Files land in `~/Documents/Koma/<title>/`. Titles in any language get their own folder.

Dialogue and sound effects are drawn by the app, not by the image model, so the words stay the ones you typed. Japanese and other CJK lettering uses a CJK font. Balloon presets set a speech balloon, a shout, a thought, or a caption in one click.

An empty manga, chapter, or character name is refused with a short message. A missing or unreadable panel image is drawn as a plain placeholder, so export still finishes. If the export folder cannot be written, if the library is open in another window, or if the library file is damaged, Koma says so instead of failing quietly. A damaged library stops at startup and asks you to move `koma.sqlite` aside.

## API key

Image and story generation use your xAI account (`grok-4.7` for the outline, `grok-imagine-image-2.0` for art).

In Settings, paste a key from [console.x.ai](https://console.x.ai). It is stored in `~/.config/koma/config.json` with permissions `600`. If `XAI_API_KEY` is already set in the environment, Koma uses that until you save a different key. With no key, drawing and planning say to add one. If the API cannot be reached, the message says so and does not include the key.

Resolution defaults to **1k**. Switch to **2k** in Settings for finished pages. Image quality is **low**, **medium**, or **auto**, and that choice is sent with the image request.

You can build the whole outline, letter the pages, and export with no key. Drawing needs one.

## Where files live

| What | Where |
| --- | --- |
| Project database | `~/.local/share/koma/koma.sqlite` |
| Generated images | `~/.local/share/koma/images/` |
| Page undo | `~/.local/share/koma/history/` |
| Exports | `~/Documents/Koma/<title>/` |

Deleting a project removes its database rows and image files.

## Get the source

```bash
git clone https://github.com/the-lord-liam/koma.git
```

The repository is public. Use Fork on GitHub to start your own copy.

## Make it your own

You're welcome to fork Koma, but you don't have to stop there. Feel free to take the idea and the concept and run with them in your own projects, whether that's building your own manga studio from scratch or remixing pieces of this one. No need to ask. If Koma sparks something, I'd love to see what you make.

## License

Koma is open source under the [MIT license](LICENSE). Fork it, change it, and share your version. The copyright notice in that file needs to stay with the copies you share.

An xAI API key stays on your machine, in `~/.config/koma/config.json` or in `XAI_API_KEY`. It is not part of this repository.
