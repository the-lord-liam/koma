# Koma

Koma is a manga studio for Ubuntu. It opens on two canvases: a manga page, and a separate illustration. The manga side plans a series, keeps character designs locked to reference art, draws one panel at a time, letters the page in code, and exports chapters. The illustration side is one picture, with no panels.

## Open it

The first launch adds **Koma** to the app grid.

```bash
~/koma/bin/koma
```

## What you do

Koma opens on the two canvases. Tap **Manga** and it asks for the title, whether the book is Japanese manga or Western comics, a page size, and the resolution. Then the page opens. Tap **Illustration** to draw a single picture. The **+** beside each name starts another canvas. Story, cast, places, and the reader are inside the manga canvas. The book’s size is remembered the next time you open that canvas.

1. **Story.** Write a premise and plan the chapters, or type the outline yourself. A plan stays as a draft until you apply it.
2. **Cast.** Describe each person, then draw a portrait. Later panels use that portrait as the identity reference. Turnaround, expression, and pose sheets are optional.
3. **Places.** Draw an establishing view for a location and attach it to panels.
4. **Pages.** Pick a layout, set the shot, camera, expression, composition, and action, then draw the panel. **Draw empty panels** fills every blank panel on the page. **Match the previous panel** sends the last drawing before this one along with the request, so a scene keeps its costumes and place. Revise a drawing, or go back to an earlier version. **Duplicate page** copies the page, including its drawings, as a separate page you can restage. **Print preview** shows the lettered page the export will use.
5. **Read.** The chapter is assembled as finished pages, in order. Click a page to edit it. Export a chapter PDF from here. Files land in `~/Documents/Koma/`.

Dialogue and sound effects are drawn by the app, not by the image model, so the words stay the ones you typed.

**Import portrait** on a character, or **Import picture** on a place, uses an image already on this machine as the reference. The first imported portrait becomes the identity later panels follow.

## API key

Image and story generation use your xAI account (`grok-4.7` for the outline, `grok-imagine-image-2.0` for art).

In Settings, paste a key from [console.x.ai](https://console.x.ai). It is stored in `~/.config/koma/config.json` with permissions `600`. If `XAI_API_KEY` is already set in the environment, Koma uses that until you save a different key.

Resolution defaults to **1k**. Switch to 2k in Settings for finished pages.

You can build the whole outline with no key. Drawing needs one.

## Where files live

| What | Where |
| --- | --- |
| Project database | `~/.local/share/koma/koma.sqlite` |
| Generated images | `~/.local/share/koma/images/` |
| Exports | `~/Documents/Koma/<title>/` |

Deleting a project removes its database rows and image files.

## Get the source

```bash
git clone https://github.com/the-lord-liam/koma.git
```

The repository is public. Use Fork on GitHub to start your own copy.

## License

Koma is open source under the [MIT license](LICENSE). Fork it, change it, and share your version. The copyright notice in that file needs to stay with the copies you share.

An xAI API key stays on your machine, in `~/.config/koma/config.json` or in `XAI_API_KEY`. It is not part of this repository.
