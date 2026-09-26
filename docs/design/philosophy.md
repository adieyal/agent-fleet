# Agent Fleet philosophy

Fleet is a personal workspace for working with many agents across machines. Its purpose is to make their work understandable and enjoyable without requiring the user to continuously reconstruct it from logs, documents, and interruptions. It is designed for one person first and may later support small teams.

## Visibility without overload

Fleet should answer the question appropriate to the user's current focus. At a glance: which projects are active, which work is paused, and what needs attention? Inside an epic: what has been done, what is happening, what remains, and what is genuinely blocked? Detailed agent traces remain available for investigation and debugging but stay out of ordinary views. Agents' many reports should become an orderly library and concise summaries, not another pile for the user to sort through. Agents should resolve routine questions themselves within their mandates, escalating according to risk and cost.

## Distributed execution, project-first view

Agents may run on different machines because of capacity or special capabilities. Fleet must preserve a coherent picture when hosts join, leave, or go offline. Projects and activity drive navigation; hosts explain where work is running and what a machine can do, but do not divide the same project into separate worlds. Fleet should normally choose or suggest a configured host from the task's needs and available capacity, with a simple user override.

## Think visually

Use space, position, colour, motion, and compact visual summaries to convey relationships and progress. A workarea can show an active slice, its plan, and its agents; an archive can show what was completed. Reading surfaces, charts, and search remain available where they express detail more clearly. The visual world should reduce the amount of prose needed to understand the state of work, not merely decorate it.

## Keep it fun and light

Fleet is not an enterprise workflow system. The default experience should feel direct and playful, with a small number of meaningful choices. Git history, synchronization, and review mechanisms can provide safety behind the scenes; they should not make the user administer branches, permissions, or approvals for routine work. Reversible documentation improvements can proceed with Git rollback available. Agents should bring the user decisions whose risk or cost calls for their judgment. Persistent roles and places give the world continuity while agent processes come and go.

These goals are a test for the [working design](workspace-hierarchy.md): a feature that increases text, interruptions, or administrative work must earn its place by making the user's understanding materially better.
