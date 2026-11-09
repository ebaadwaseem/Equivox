# Equivox

Equivox lets you control a web browser by speaking. You can open tabs, search the web, click links, scroll through pages, and fill in forms. It listens to partial speech so a complete command can start while you continue talking.

For example, “open a new tab, then search for physiotherapy clinics in Edmonton, okay now scroll down” becomes a sequence of actions. After each action, Equivox checks the resulting page before deciding what to do next.

Apple Speech transcribes the audio on the Mac. Jev receives the transcript and page context, chooses an action, and judges whether the command is ready. Python checks the decision, and Playwright carries it out. The Vox panel shows the transcript and action history, including which recent actions the model receives as context.

The current build supports common browser tasks and form controls. More work remains on noisy speech, complex page widgets, and measuring the full delay from speech to action.
