// Shared vocabulary and consequences for both focus controls.
export function focusTitle(name, focus) {
  return focus === 'background'
    ? `Put ${name} in background: dim its room and remove its androids from the deck. Running work continues; choose priority to show active androids again.`
    : `Put ${name} in priority: brighten its room and show its active androids on the deck. Running work continues; choose background to hide them again.`;
}
export function focusFeedback(name, focus) {
  return `${name} is in ${focus}. ${focus === 'background' ? 'Its androids are hidden' : 'Its active androids are shown'}; running work continues. Choose ${focus === 'background' ? 'priority' : 'background'} to reverse this.`;
}
