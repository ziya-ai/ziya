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
    return items.map((item, index) => {
        const type = item.type;

        switch (type) {
            case 'alert':
                return (
                    <div key={index} className="alert-wrapper">
                        <span>{item.message}</span>
                    </div>
                );

            case 'status':
                if (!item.text) return null;
                return (
                    <div key={index} className="status-block">
                        <ItemBlock data={item} index={index} />
                    </div>
                );

            case 'warning':
                return (
                    <span key={index} className="warning-text">
                        {item.content}
                    </span>
                );

            case 'info':
                return <p key={index}>{item.content}</p>;

            case 'heading':
                return <h2 key={index}>{item.title}</h2>;

            case 'divider':
                return <hr key={index} />;

            default:
                return <span key={index}>{item.text || ''}</span>;
        }
    });
};

export default renderItems;
