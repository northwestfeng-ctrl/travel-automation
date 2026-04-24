// 携程ebooking IM 会话列表提取 - 基于实测DOM结构
// 实测类名: styles_groupInfo__3gsOd / styles_groupTitle__1JY5U / styles_groupDesc__3WxMv
// 规则: groupDesc 是民宿回复语=已回复跳过；否则=客人消息需回复
// 工作人员: 王依凡/石寿霞/徐沐凡/智能客服/人工客服/官方帮助 不在处理范围
window.__ctripExtractSessions = function() {
    var results = [];

    // 民宿已知回复语（出现在 groupDesc 说明已回复，跳过）
    var hotelReplyPatterns = [
        '亲，有什么问题可以一并留言，稍后我来解答',
        '亲，有什么问题可以一并留言',
        '亲，等等哈',
        '手头有点事儿，我稍微离开一下。回头我会回复您。',
        '回头我会回复您',
        '请问想预订什么日期呢',
        '稍后主动联系您',
        '我这边先帮您记录',
    ];

    function isHotelReply(text) {
        for (var i = 0; i < hotelReplyPatterns.length; i++) {
            if (text.indexOf(hotelReplyPatterns[i]) !== -1) return true;
        }
        return false;
    }

    function isStaff(name) {
        var staff = ['王依凡', '石寿霞', '徐沐凡', '智能客服', '人工客服', '官方帮助'];
        return staff.indexOf(name) !== -1;
    }

    // 遍历会话列表项: class 含 "groupInfo"
    var items = document.querySelectorAll('[class*="groupInfo"]');
    for (var item of items) {
        var titleEl = item.querySelector('[class*="groupTitle"]');
        var descEl = item.querySelector('[class*="groupDesc"]');

        if (!titleEl) continue;
        var name = (titleEl.textContent || '').trim();
        var desc = descEl ? (descEl.textContent || '').trim() : '';

        // 跳过工作人员
        if (isStaff(name)) continue;

        // 如果最新消息是民宿回复，说明客人没新消息
        if (isHotelReply(desc)) continue;

        // 去重
        var dup = results.some(function(r) { return r.name === name; });
        if (dup) continue;

        results.push({
            name: name,
            text: desc,
            consultSid: '',
            latestGuestMsg: desc
        });
    }

    return results;
};
