function handler(type: string) {
  switch (type) {
    case 'a':
      return handleA();
    case 'b':
      return handleB();
    case 'c':
      return handleC();
    case 'd':
      return handleD();
    default:
      return null;
  }
}

function newFunction() {
  const data = getData();
  if (!data) {
    return 'no data';
  }
  
  const result = data.map((item: any) => {
    const id = item.id || '';
    const name = item.name || 'Untitled';
    return `${id}: ${name}`;
  }).join('\n');
  
  return result;
}

function existingFunction() {
  return 'existing';
}
