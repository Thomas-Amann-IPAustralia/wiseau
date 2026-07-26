/*
 * Deployment configuration.
 *
 * Set this to your deployed backend URL (the Hugging Face Space). This is the
 * one file you edit per deployment — app.js reads it and never needs changing.
 * Example: "https://your-user-your-space.hf.space"
 *
 * The GitHub Pages workflow overwrites the line below in the *published* copy
 * when the MARKDOWN_API_BASE repository variable is set, so the deployed site
 * can point at a Space without this default changing (ADR-023). If the live
 * site talks to an unexpected backend, check that variable first.
 */
window.MARKDOWN_API_BASE = "http://localhost:7860";
