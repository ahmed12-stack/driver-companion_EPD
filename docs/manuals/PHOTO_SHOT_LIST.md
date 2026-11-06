# Photos and links for the CARA DC-1 manuals

Put each file in the `photos/` folder with exactly this name, then recompile.
A missing photo prints a dashed frame with its file name, so you can see what is still needed.

| File | Used in | What to shoot |
| --- | --- | --- |
| `qsg_product.jpg` | Quick Start Guide, B | Whole unit (case + speaker pod), straight front view, plain white background, landscape about 2:1. PAIR and TRIP visible on top. Callouts 1-6 are placed on top of it, so send it to Claude to line them up |
| `um_title.jpg` | Manual, title page | The unit on a car dashboard, windscreen behind, landscape about 16:10 |
| `um_front.jpg` | Manual, Figure 2.2 | Front of the unit, white background |
| `um_top.jpg` | Manual, Figure 2.2 | Top of the unit: PAIR button and TRIP switch with their labels |
| `app_home.png` | Manual, Figure 2.3a | Phone screenshot: home screen, Connected, green Phone GPS line, a few trips |
| `app_trip.png` | Manual, Figure 2.3b | Phone screenshot: one trip's detail screen with the map |
| `app_settings.png` | Manual, Figure 2.3c | Phone screenshot: Announcements section |

Tips: daylight or two lamps from the sides, no flash, lens at the height of the unit, fill the frame.
Screenshots: take them from the new app (version 0.3.0).

## Links (top of each .tex file)

| Field | Where | Effect |
| --- | --- | --- |
| `\VideoURL` | Quick Start Guide only | Prints the QR code in box H |
| `\AppURL` | Quick Start Guide | QR code for the app download |
| `\Website` | both | Printed text and a QR code in box F |
| `\SupportMail` | both | Printed support e-mail |

## Build

    pdflatex CARA_DC-1_Quick_Start_Guide.tex        (must stay 1 page)
    pdflatex CARA_DC-1_User_Manual.tex
    makeindex -s cara-index.ist CARA_DC-1_User_Manual.idx
    pdflatex CARA_DC-1_User_Manual.tex
    pdflatex CARA_DC-1_User_Manual.tex

Overleaf: upload the folder (with `photos/`) and set the compiler to pdfLaTeX.
