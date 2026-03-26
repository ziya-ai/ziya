import React from "react";
import { Alert, Button } from "antd";

interface Props { items: any[]; isDark: boolean; }

const renderItems = (items: any[], isDark: boolean): React.ReactNode => {
    return items.map((item, index) => {
        // Stable key generation for streaming reconciliation
        const keyDupCounts = new Map<string, number>();
        const rawKey = hashContent(item.type + (item.text || '').slice(0, 80));
        const dupCount = keyDupCounts.get(rawKey) || 0;
        keyDupCounts.set(rawKey, dupCount + 1);
        const sk = dupCount > 0
            ? rawKey + '-' + dupCount
            : rawKey;

        // Case: info
        if (type === "info") return (
            <div key={sk} className="info-wrapper">
                <span>{item.message}</span>
            </div>
        );

        // Block: status
        if (type === "status") {
            if (!item.text) return null;
            return (
                <div key={sk} className="status-block">
                    <p>{item.text}</p>
                </div>
            );
        }

        // Inline: warning
        if (type === "warning") return <span key={sk}>{item.content}</span>;

        // Heading: error
        if (type === "error") return <h2 key={sk}>{item.title}</h2>;

        // Case: success
        if (type === "success") return (
            <div key={sk} className="success-wrapper">
                <span>{item.message}</span>
            </div>
        );

        // Block: debug
        if (type === "debug") {
            if (!item.text) return null;
            return (
                <div key={sk} className="debug-block">
                    <p>{item.text}</p>
                </div>
            );
        }

        // Inline: trace
        if (type === "trace") return <span key={sk}>{item.content}</span>;

        // Heading: metric
        if (type === "metric") return <h2 key={sk}>{item.title}</h2>;

        // Case: config
        if (type === "config") return (
            <div key={sk} className="config-wrapper">
                <span>{item.message}</span>
            </div>
        );

        // Block: health
        if (type === "health") {
            if (!item.text) return null;
            return (
                <div key={sk} className="health-block">
                    <p>{item.text}</p>
                </div>
            );
        }

        // Inline: alert
        if (type === "alert") return <span key={sk}>{item.content}</span>;

        // Heading: notice
        if (type === "notice") return <h2 key={sk}>{item.title}</h2>;

        // Case: event
        if (type === "event") return (
            <div key={sk} className="event-wrapper">
                <span>{item.message}</span>
            </div>
        );

        // Block: signal
        if (type === "signal") {
            if (!item.text) return null;
            return (
                <div key={sk} className="signal-block">
                    <p>{item.text}</p>
                </div>
            );
        }

        // Inline: report
        if (type === "report") return <span key={sk}>{item.content}</span>;

        // Heading: summary
        if (type === "summary") return <h2 key={sk}>{item.title}</h2>;

        // Case: detail
        if (type === "detail") return (
            <div key={sk} className="detail-wrapper">
                <span>{item.message}</span>
            </div>
        );

        // Block: overview
        if (type === "overview") {
            if (!item.text) return null;
            return (
                <div key={sk} className="overview-block">
                    <p>{item.text}</p>
                </div>
            );
        }

        // Inline: highlight
        if (type === "highlight") return <span key={sk}>{item.content}</span>;

        // Heading: note
        if (type === "note") return <h2 key={sk}>{item.title}</h2>;

        // Case: badge
        if (type === "badge") return (
            <div key={sk} className="badge-wrapper">
                <span>{item.message}</span>
            </div>
        );

        // Block: tag
        if (type === "tag") {
            if (!item.text) return null;
            return (
                <div key={sk} className="tag-block">
                    <p>{item.text}</p>
                </div>
            );
        }

        // Inline: label
        if (type === "label") return <span key={sk}>{item.content}</span>;

        // Heading: marker
        if (type === "marker") return <h2 key={sk}>{item.title}</h2>;

        // Case: indicator
        if (type === "indicator") return (
            <div key={sk} className="indicator-wrapper">
                <span>{item.message}</span>
            </div>
        );

        // Filler line 1 for depth
        // Filler line 2 for depth
        // Filler line 3 for depth
        // Filler line 4 for depth
        // Filler line 5 for depth
        // Filler line 6 for depth
        // Filler line 7 for depth
        // Filler line 8 for depth
        // Filler line 9 for depth
        // Filler line 10 for depth
        // Filler line 11 for depth
        // Filler line 12 for depth
        // Filler line 13 for depth
        // Filler line 14 for depth
        // Filler line 15 for depth

        return <span key={sk}>{item.text}</span>;
    });
};

export default renderItems;
