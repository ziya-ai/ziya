function handler(type: string) {
  switch (type) {
    case 'a':
      return handleA();
    case 'b':
      return handleB();
    case 'c':
      // Old handling
      return null;
    default:
      return null;
  }
}

function existingFunction() {
  return 'existing';
}
