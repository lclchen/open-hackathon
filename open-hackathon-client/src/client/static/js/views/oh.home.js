/*
 * Copyright (c) Microsoft Open Technologies (Shanghai) Co. Ltd.  All rights reserved.
 *
 * The MIT License (MIT)
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

(function ($, oh) {

    function pageLoad() {
        getTalentList().then(function (data) {
            if (data.error) {

            } else {
                $('#talent_list').append($('#talent_list_template').tmpl(data.splice(0, 6)));
            }
        });

        getGrantedawards().then(function (data) {
            if (data.error) {

            } else {
                var infn3 = $('#info3');
                var tabs = infn3.find('.oh-tabs').append($('#award_title_template').tmpl(data));
                infn3.find('.tab-content').append($('#award_list_template').tmpl(data));
                tabs.tab();
                tabs.find('a:eq(0)').trigger('click');
                infn3.find('.carousel').each(function (i, o) {
                    $(o).find('.carousel-indicators>li:eq(0)').addClass('active');
                    $(o).find('.carousel-inner>.item:eq(0)').addClass('active');
                })
                infn3.find('.carousel').carousel();
                infn3.removeClass('hide');
            }
        })
    }

    function bindEvent() {

    }

    function getTalentList() {
        return oh.api.talent.list.get();
    }

    function getGrantedawards() {
        return oh.api.grantedawards.get({query: {limit: 4}});
    }

    function init() {
        pageLoad();
        bindEvent();
    }

    $(function () {
        init();
    });

})(window.jQuery, window.oh);
