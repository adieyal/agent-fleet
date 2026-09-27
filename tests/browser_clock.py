"""Drive animation to an observable state without waiting for real time."""

from typing import Any

from playwright.sync_api import Page


def advance_until(page: Page, expression: str) -> Any:
    page.wait_for_function("window.fleetDeck !== undefined")
    return page.evaluate("""expression => {
        const read = new Function(`return (${expression})`);
        for (let step = 0; step <= 1200; step++) {
            const result = read();
            if (result) {
                fleetDeck.advanceTime(0);
                return result;
            }
            fleetDeck.advanceTime(0.1, false);
        }
        throw new Error(`Animation did not reach: ${expression}`);
    }""", expression)
