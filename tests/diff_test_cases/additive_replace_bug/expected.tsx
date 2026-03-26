import React from 'react';

// Utility function
function hashContent(text: string): string {
    let h = 5381;
    for (let i = 0; i < text.length; i++) {
        h = ((h << 5) + h + text.charCodeAt(i)) | 0;
    }
    return (h >>> 0).toString(36);
}

interface ItemProps {
    data: any;
    index: number;
}

const ItemBlock: React.FC<ItemProps> = ({ data, index }) => {
    return <div className="item-block">{data.content}</div>;
};

const renderItems = (items: any[], isDark: boolean): React.ReactNode => {
    // Build stable keys so React can reconcile elements correctly
    // when items are inserted/removed during streaming.
    const keyDupCounts = new Map<string, number>();
    return items.map((item, index) => {
        const rawKey = hashContent(item.type + (item.text || item.content || '').slice(0, 80));
        const dupCount = keyDupCounts.get(rawKey) || 0;
        keyDupCounts.set(rawKey, dupCount + 1);
        const sk = dupCount > 0
            ? `${rawKey}-${dupCount}`
            : rawKey;

        const type = item.type;

        switch (type) {
            case 'alert':
                return (
                    <div key={sk} className="alert-wrapper">
                        <span>{item.message}</span>
                    </div>
                );

            case 'status':
                if (!item.text) return null;
                return (
                    <div key={sk} className="status-block">
                        <ItemBlock data={item} index={index} />
                    </div>
                );

            case 'warning':
                return (
                    <span key={sk} className="warning-text">
                        {item.content}
                    </span>
                );

             case 'info':
                return <p key={sk}>{item.content}</p>;

            case 'heading':
                return <h2 key={sk}>{item.title}</h2>;

            case 'divider':
                return <hr key={sk} />;

            default:
                return <span key={sk}>{item.text || ''}</span>;
        }
    });
};

export default renderItems;
